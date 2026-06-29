"""
Motor de coleta de posts do Threads via GraphQL interno e Playwright.

Define :class:`ThreadsScraper`, que orquestra a obtenção de sessão,
a busca por keyword, a coleta de posts de usuários e a extração de
comentários/replies. As funções de parsing (puras) ficam em
:mod:`parser`; tudo mais (sessão, browser, cliente GraphQL) está
consolidado neste arquivo.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import requests
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

import parser as _parser
from config import (
    BASE_URL,
    COOKIES_FILE,
    DOC_ID_REPLIES,
    DOC_ID_SEARCH,
    DOC_ID_USER_THREADS,
    DOC_IDS_FILE,
    GRAPHQL_URL,
    INTERNAL_DIR,
    PAGE_SIZE,
    STEALTH_SCRIPT,
    THREADS_PASSWORD,
    THREADS_USERNAME,
    TOKENS_FILE,
    random_user_agent,
)


# ===========================================================================
# Sessão — utilitários de cookie e persistência em disco
# ===========================================================================

def cookies_to_header(cookies: list[dict]) -> str:
    """Converte cookies do Playwright para o formato de header HTTP."""
    return "; ".join(
        f"{c['name']}={c['value']}"
        for c in cookies
        if c.get("name") and c.get("value")
    )


def has_auth_cookie(cookies: list[dict] | str) -> bool:
    """Indica se ``sessionid`` (login autenticado) está presente."""
    if isinstance(cookies, str):
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith("sessionid=") and len(part) > len("sessionid="):
                return True
        return False
    if isinstance(cookies, list):
        for c in cookies:
            if c.get("name") == "sessionid" and c.get("value"):
                return True
    return False


def load_doc_ids(
    doc_id_search:  str = "",
    doc_id_user:    str = "",
    doc_id_replies: str = "",
    ttl_hours:      float = 24.0,
) -> dict[str, str]:
    """Carrega doc_ids GraphQL salvos, ou devolve os defaults."""
    result = {
        "search":  doc_id_search,
        "user":    doc_id_user,
        "replies": doc_id_replies,
    }
    if not DOC_IDS_FILE.exists():
        return result
    try:
        with open(DOC_IDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("saved_at", 0) > ttl_hours * 3600:
            print("  doc_ids expirados (> 24h). Redescoberta necessária.")
            return result
        result["search"]  = data.get("search")  or doc_id_search
        result["user"]    = data.get("user")    or doc_id_user
        result["replies"] = data.get("replies") or doc_id_replies
        if result["search"]:
            print(f"  doc_ids carregados (search={result['search']})")
    except Exception:
        pass
    return result


def save_doc_ids(
    doc_id_search:  str,
    doc_id_user:    str,
    doc_id_replies: str,
) -> None:
    """Persiste doc_ids descobertos em disco."""
    try:
        with open(DOC_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "search":   doc_id_search,
                    "user":     doc_id_user,
                    "replies":  doc_id_replies,
                    "saved_at": time.time(),
                },
                f,
                indent=2,
            )
    except Exception:
        pass


def save_session(
    cookies:   list[dict],
    lsd_token: str,
    fb_dtsg:   str,
    device_id: str,
    app_id:    str,
) -> None:
    """Salva cookies e tokens em disco (`COOKIES_FILE` + `TOKENS_FILE`)."""
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "lsd":       lsd_token,
                    "fb_dtsg":   fb_dtsg,
                    "device_id": device_id,
                    "app_id":    app_id,
                    "saved_at":  time.time(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def load_session(
    default_app_id: str = "238260118697367",
    ttl_hours:      float = 6.0,
) -> dict[str, Any] | None:
    """Carrega a sessão salva, se ainda válida."""
    if not COOKIES_FILE.exists() or not TOKENS_FILE.exists():
        return None
    try:
        with open(COOKIES_FILE, encoding="utf-8") as f:
            cookies = json.load(f)
        with open(TOKENS_FILE, encoding="utf-8") as f:
            tokens = json.load(f)
        if time.time() - tokens.get("saved_at", 0) > ttl_hours * 3600:
            print("  Sessão expirada (> 6h). Renovando …")
            return None
        lsd = tokens.get("lsd", "")
        if not lsd:
            return None
        return {
            "cookies":     cookies,
            "cookies_str": cookies_to_header(cookies),
            "lsd":         lsd,
            "fb_dtsg":     tokens.get("fb_dtsg", ""),
            "device_id":   tokens.get("device_id", ""),
            "app_id":      tokens.get("app_id", default_app_id),
        }
    except Exception:
        return None


# ===========================================================================
# Browser — Playwright stealth, login e inspeção de DOM
# ===========================================================================

async def launch_stealth_browser(
    p,
    user_agent: str,
    headless:   bool = True,
) -> tuple[Browser, BrowserContext, Page]:
    """Inicia Chromium com stealth e contexto pt-BR/America/Sao_Paulo."""
    browser: Browser = await p.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--ignore-certificate-errors",
            "--disable-web-security",
            "--lang=pt-BR",
        ],
    )
    context: BrowserContext = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1366, "height": 768},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        extra_http_headers={
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    await context.add_init_script(STEALTH_SCRIPT)
    page: Page = await context.new_page()
    return browser, context, page


async def perform_login(
    page:     Page,
    username: str,
    password: str,
) -> bool:
    """
    Executa o fluxo de login do Threads via Playwright.

    Sucesso é confirmado pela presença do cookie ``sessionid``.
    Retorna ``False`` em 2FA, captcha ou credenciais inválidas.
    """
    if not username or not password:
        return False

    print(f"  Fazendo login como {username} …")
    try:
        await page.goto(
            f"{BASE_URL}/login",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2.0, 3.5))

        for txt in (
            "Allow all cookies", "Permitir todos",
            "Accept all", "Aceitar todos", "Allow essential",
        ):
            try:
                btn = page.get_by_role("button", name=re.compile(txt, re.I))
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        user_selectors = [
            'input[autocomplete="username"]',
            'input[name="username"]',
            'input[placeholder*="sername"]',
            'input[placeholder*="suário"]',
            'input[placeholder*="mail"]',
            'input[type="text"]',
        ]
        filled_user = False
        for sel in user_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=3000)
                await loc.fill(username, timeout=5000)
                filled_user = True
                break
            except Exception:
                continue
        if not filled_user:
            print("  Campo de usuário não encontrado na página de login.")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.2))

        pwd_selectors = [
            'input[autocomplete="current-password"]',
            'input[name="password"]',
            'input[type="password"]',
        ]
        filled_pwd = False
        for sel in pwd_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=3000)
                await loc.fill(password, timeout=5000)
                filled_pwd = True
                break
            except Exception:
                continue
        if not filled_pwd:
            print("  Campo de senha não encontrado na página de login.")
            return False

        await asyncio.sleep(random.uniform(0.4, 1.0))

        submitted = False
        submit_selectors = [
            'div[role="button"]:has-text("Log in")',
            'div[role="button"]:has-text("Entrar")',
            'button[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Entrar")',
        ]
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=3000)
                submitted = True
                break
            except Exception:
                continue
        if not submitted:
            try:
                await page.keyboard.press("Enter")
                submitted = True
            except Exception:
                pass
        if not submitted:
            print("  Não foi possível submeter o formulário de login.")
            return False

        try:
            await page.wait_for_url(
                re.compile(
                    r"https?://(www\.)?threads\.(com|net)/(?!login).*"
                ),
                timeout=30_000,
            )
        except Exception:
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
        await asyncio.sleep(random.uniform(3.0, 5.0))

        for _ in range(3):
            dismissed = False
            for txt in (
                "Not now", "Agora não", "Not Now",
                "Maybe later", "Mais tarde",
            ):
                try:
                    btn = page.get_by_role(
                        "button", name=re.compile(txt, re.I)
                    )
                    if await btn.count() > 0:
                        await btn.first.click(timeout=2000)
                        dismissed = True
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                        break
                except Exception:
                    continue
            if not dismissed:
                break

        cookies = await page.context.cookies()
        if has_auth_cookie(cookies):
            print("  Login bem-sucedido ✓")
            return True

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(2.0, 3.5))
        except Exception:
            pass

        cookies = await page.context.cookies()
        if has_auth_cookie(cookies):
            print("  Login bem-sucedido ✓")
            return True

        print(
            "  Login falhou: sessionid não recebido "
            "(possível 2FA, captcha ou credenciais inválidas)."
        )
        return False

    except Exception as exc:
        print(f"  Erro durante o login: {exc}")
        return False


async def count_dom_posts(page: Page) -> int:
    """Conta posts renderizados no DOM (heurística por seletor)."""
    try:
        return await page.evaluate("""
            () => {
                const sels = [
                    '[data-pressable-container="true"]',
                    'article',
                    'div[role="article"]',
                    'a[href*="/post/"]',
                    'a[href*="/t/"]',
                ];
                let max = 0;
                for (const s of sels) {
                    const n = document.querySelectorAll(s).length;
                    if (n > max) max = n;
                }
                return max;
            }
        """)
    except Exception:
        return 0


# ===========================================================================
# Cliente HTTP GraphQL
# ===========================================================================

def build_headers(
    lsd_token:     str,
    fb_dtsg:       str,
    app_id:        str,
    user_agent:    str,
    cookies_str:   str,
    referer:       str = "",
    friendly_name: str = "BarcelonaSearchResultsQuery",
) -> dict[str, str]:
    """Headers para o endpoint ``/api/graphql`` do Threads/Meta."""
    headers: dict[str, str] = {
        "User-Agent":          user_agent,
        "Accept":              "*/*",
        "Accept-Language":     "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type":        "application/x-www-form-urlencoded",
        "Origin":              BASE_URL,
        "Referer":             referer or BASE_URL,
        "Cookie":              cookies_str,
        "X-FB-LSD":            lsd_token,
        "X-IG-App-ID":         app_id,
        "X-ASBD-ID":           "129477",
        "X-FB-Friendly-Name":  friendly_name,
        "Sec-Fetch-Site":      "same-origin",
        "Sec-Fetch-Mode":      "cors",
        "Sec-Fetch-Dest":      "empty",
        "Sec-Ch-Ua":           '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile":    "?0",
        "Sec-Ch-Ua-Platform":  '"Windows"',
    }
    if fb_dtsg:
        headers["X-FB-DTSG"] = fb_dtsg
    return headers


def graphql_request(
    doc_id:        str,
    variables:     dict,
    lsd_token:     str,
    fb_dtsg:       str,
    app_id:        str,
    user_agent:    str,
    cookies_str:   str,
    friendly_name: str = "",
    referer:       str = "",
    graphql_url:   str = GRAPHQL_URL,
) -> dict | None:
    """
    Executa uma query GraphQL no endpoint interno do Threads.

    O body é ``application/x-www-form-urlencoded`` (não JSON):
    ``variables=<json>&doc_id=<id>&lsd=<token>``.
    Retorna ``None`` em caso de erro HTTP, timeout ou resposta inesperada.
    """
    headers = build_headers(
        lsd_token=lsd_token,
        fb_dtsg=fb_dtsg,
        app_id=app_id,
        user_agent=user_agent,
        cookies_str=cookies_str,
        referer=referer,
        friendly_name=friendly_name or "BarcelonaSearchResultsQuery",
    )

    csrf = ""
    for part in cookies_str.split(";"):
        part = part.strip()
        if part.startswith("csrftoken="):
            csrf = part.split("=", 1)[1]
            break

    body_parts: dict[str, str] = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "doc_id":    doc_id,
        "lsd":       lsd_token,
    }
    if fb_dtsg:
        body_parts["fb_dtsg"] = fb_dtsg
    if csrf:
        body_parts["fb_dtsg_ag"] = csrf

    body = urlencode(body_parts)

    try:
        r = requests.post(
            graphql_url,
            data=body,
            headers=headers,
            timeout=30,
        )

        if r.status_code == 429:
            print("    Rate limit (429). Aguardando 60s …")
            time.sleep(60)
            return None

        if r.status_code == 403:
            print("    Acesso negado (403). Tokens podem ter expirado.")
            return None

        if r.status_code != 200:
            print(f"    HTTP {r.status_code}: {r.text[:200]}")
            return None

        ct = r.headers.get("content-type", "")
        if "json" not in ct and "javascript" not in ct:
            print(
                f"    Resposta não-JSON (content-type: {ct}). "
                "Tokens podem estar inválidos."
            )
            return None

        text = r.text.strip()
        if not text or text[0] not in ('{', '['):
            print(f"    Resposta inesperada: {text[:120]}")
            return None

        return r.json()

    except requests.exceptions.Timeout:
        print("    Timeout na requisição GraphQL.")
        return None
    except Exception as e:
        print(f"    Erro GraphQL: {e}")
        return None


class ThreadsScraper:
    """
    Coleta posts do Threads via GraphQL interno e Playwright stealth.

    Fluxo principal:

    1. :meth:`_obtain_session` → abre o Threads com Playwright para capturar
       cookies, ``lsd``, ``fb_dtsg`` e ``doc_ids`` da API GraphQL.
    2. :meth:`search_posts` → pagina resultados por keyword usando scroll
       real no browser + paginação direta via HTTP GraphQL como fallback.
    3. :meth:`fetch_user_posts` → coleta posts de um usuário específico.
    4. :meth:`fetch_comments` / :meth:`fetch_comments_all` → busca replies
       dos posts coletados.

    Parameters
    ----------
    headless : bool, optional
        Se ``True``, executa o browser sem interface gráfica. Padrão: ``True``.
    search_term : str, optional
        Termo de busca inicial, usado durante a obtenção de sessão para
        navegar direto para a SERP e capturar doc_ids relevantes.

    Attributes
    ----------
    posts : dict[str, dict]
        Dicionário de posts coletados, indexados pela URL canônica.
    headless : bool
        Indica se o browser roda em modo headless.
    user_agent : str
        User-Agent sorteado aleatoriamente para esta instância.
    """

    def __init__(self, headless: bool = True, search_term: str = "") -> None:
        self.headless   = headless
        self.user_agent = random_user_agent()
        self.posts: dict[str, dict] = {}

        self._cookies_str: str = ""
        self._lsd_token:   str = ""
        self._fb_dtsg:     str = ""
        self._device_id:   str = ""
        self._app_id:      str = "238260118697367"

        self._doc_id_search:  str = DOC_ID_SEARCH
        self._doc_id_user:    str = DOC_ID_USER_THREADS
        self._doc_id_replies: str = DOC_ID_REPLIES

        self._search_gql_ctx: dict = {
            "doc_id":        "",
            "variables":     {},
            "friendly_name": "",
        }
        self._all_captured_gql: list[dict] = []
        self._latest_cursor: str = ""

        self._search_term = search_term or "default"
        self._load_doc_ids()

    # ------------------------------------------------------------------
    # doc_ids — persistência
    # ------------------------------------------------------------------

    def _load_doc_ids(self) -> None:
        """
        Carrega doc_ids GraphQL descobertos anteriormente do cache em disco.

        Atualiza :attr:`_doc_id_search`, :attr:`_doc_id_user` e
        :attr:`_doc_id_replies` se o cache for válido (< 24h).
        """
        ids = load_doc_ids(
            doc_id_search=self._doc_id_search,
            doc_id_user=self._doc_id_user,
            doc_id_replies=self._doc_id_replies,
        )
        self._doc_id_search  = ids["search"]
        self._doc_id_user    = ids["user"]
        self._doc_id_replies = ids["replies"]

    def _save_doc_ids(self) -> None:
        """
        Persiste os doc_ids GraphQL atuais em disco para reutilização futura.
        """
        save_doc_ids(
            doc_id_search=self._doc_id_search,
            doc_id_user=self._doc_id_user,
            doc_id_replies=self._doc_id_replies,
        )

    # ------------------------------------------------------------------
    # Sessão — persistência
    # ------------------------------------------------------------------

    def _save_session(self, cookies: list) -> None:
        """
        Persiste cookies e tokens de autenticação em disco.

        Parameters
        ----------
        cookies : list
            Lista de cookies do Playwright a salvar.
        """
        save_session(
            cookies=cookies,
            lsd_token=self._lsd_token,
            fb_dtsg=self._fb_dtsg,
            device_id=self._device_id,
            app_id=self._app_id,
        )

    def _load_session(self) -> bool:
        """
        Tenta carregar uma sessão salva em disco.

        Returns
        -------
        bool
            ``True`` se a sessão foi carregada com sucesso e o token
            ``lsd`` está presente.
        """
        data = load_session(
            default_app_id=self._app_id,
        )
        if not data:
            return False
        self._cookies_str = data["cookies_str"]
        self._lsd_token   = data["lsd"]
        self._fb_dtsg     = data["fb_dtsg"]
        self._device_id   = data["device_id"]
        self._app_id      = data["app_id"]
        return True

    # ------------------------------------------------------------------
    # Login e obtenção de sessão via Playwright
    # ------------------------------------------------------------------

    async def _obtain_session(self) -> bool:
        """
        Abre o Threads com Playwright para capturar tokens e doc_ids.

        Tenta reutilizar a sessão salva antes de abrir o browser. Se a
        sessão salva for inválida, inicia uma nova sessão navegando até
        a SERP do termo de busca e interceptando os requests/responses
        da API GraphQL.

        Returns
        -------
        bool
            ``True`` se a sessão foi obtida com sucesso (token ``lsd``
            presente).
        """
        if self._load_session():
            if await self._test_graphql():
                print("  Sessão anterior válida ✓")
                return True
            print("  Sessão inválida. Renovando …")

        print("  Obtendo sessão via Playwright …")

        async with async_playwright() as p:
            browser, context, page = await launch_stealth_browser(
                p, self.user_agent, self.headless
            )

            captured_tokens: dict = {}
            captured_doc_ids: dict = {}
            captured_search_data: list = []

            async def _on_request(request):
                try:
                    if request.method != "POST":
                        return
                    if "/api/graphql" not in request.url:
                        return
                    import urllib.parse
                    params   = urllib.parse.parse_qs(request.post_data or "")
                    doc_id   = (params.get("doc_id") or [None])[0]
                    vars_str = (params.get("variables") or [""])[0]
                    if doc_id and vars_str:
                        try:
                            variables = json.loads(vars_str)
                            if "query" in variables or "searchWord" in variables:
                                captured_doc_ids["search"] = doc_id
                            elif "userID" in variables:
                                captured_doc_ids["user"] = doc_id
                            elif "postID" in variables:
                                captured_doc_ids["replies"] = doc_id
                        except Exception:
                            pass
                except Exception:
                    pass

            async def _on_response(response):
                try:
                    url = response.url
                    ct  = response.headers.get("content-type", "")

                    if "threads.net" in url and "text/html" in ct:
                        try:
                            body  = await response.text()
                            found = _parser.extract_tokens_from_html(body)
                            for k, v in found.items():
                                if v and not captured_tokens.get(k):
                                    captured_tokens[k] = v
                        except Exception:
                            pass

                    if "/api/graphql" in url and (
                        "json" in ct or "javascript" in ct
                    ):
                        try:
                            data = await response.json()
                            did  = (
                                data.get("device_id")
                                or data.get("data", {}).get("device_id")
                            )
                            if did:
                                captured_tokens["device_id"] = did
                            if isinstance(data, dict) and "data" in data:
                                captured_search_data.append(data)
                        except Exception:
                            pass

                    if url.endswith(".js") or "rsrc.php" in url or "bundle" in url:
                        try:
                            js = await response.text()
                            if len(js) > 500_000:
                                for m in re.finditer(
                                    r'"doc_id"\s*:\s*"(\d{15,})"', js
                                ):
                                    did = m.group(1)
                                    if did not in captured_doc_ids.values():
                                        captured_doc_ids.setdefault(
                                            "_bundle_ids", []
                                        ).append(did)
                        except Exception:
                            pass
                except Exception:
                    pass

            page.on("request", _on_request)
            page.on("response", _on_response)

            try:
                await page.goto(
                    BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await asyncio.sleep(random.uniform(3, 5))

                if THREADS_USERNAME and THREADS_PASSWORD:
                    current_cookies = await context.cookies()
                    if not has_auth_cookie(current_cookies):
                        await perform_login(page, THREADS_USERNAME, THREADS_PASSWORD)

                slug = quote(self._search_term)
                await page.goto(
                    f"{BASE_URL}/search?q={slug}&serp_type=default",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=25_000)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(3, 5))

                if not captured_tokens.get("lsd") or not captured_tokens.get("fb_dtsg"):
                    try:
                        page_tokens = await page.evaluate("""
                            () => {
                                const result = {};
                                const sjsScripts = document.querySelectorAll(
                                    'script[type="application/json"][data-sjs]'
                                );
                                for (const el of sjsScripts) {
                                    const txt = el.textContent || '';
                                    if (!result.lsd) {
                                        const m = txt.match(/"LSD"[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                        if (m) result.lsd = m[1];
                                    }
                                    if (!result.lsd) {
                                        const m = txt.match(/"lsd"\\s*:\\s*\\{[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                        if (m) result.lsd = m[1];
                                    }
                                    if (!result.fb_dtsg) {
                                        const m = txt.match(/"DTSGInitialData"[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                        if (m) result.fb_dtsg = m[1];
                                    }
                                    if (!result.fb_dtsg) {
                                        const m = txt.match(/"dtsg"\\s*:\\s*\\{[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                        if (m) result.fb_dtsg = m[1];
                                    }
                                    if (!result.app_id) {
                                        const m = txt.match(/"APP_ID"\\s*:\\s*"?(\\d{10,})"?/);
                                        if (m) result.app_id = m[1];
                                    }
                                }
                                if (!result.lsd || !result.fb_dtsg) {
                                    const allScripts = document.querySelectorAll('script');
                                    for (const s of allScripts) {
                                        const txt = s.textContent || '';
                                        if (!result.lsd) {
                                            const m = txt.match(/"LSD"[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                            if (m) result.lsd = m[1];
                                        }
                                        if (!result.fb_dtsg) {
                                            const m = txt.match(/"DTSGInitialData"[^}]*"token"\\s*:\\s*"([^"]+)"/);
                                            if (m) result.fb_dtsg = m[1];
                                        }
                                    }
                                }
                                if (!result.lsd) {
                                    const inp = document.querySelector('input[name="lsd"]');
                                    if (inp) result.lsd = inp.value;
                                }
                                if (!result.fb_dtsg) {
                                    const inp = document.querySelector('input[name="fb_dtsg"]');
                                    if (inp) result.fb_dtsg = inp.value;
                                }
                                return result;
                            }
                        """)
                        if page_tokens:
                            for k, v in page_tokens.items():
                                if v and not captured_tokens.get(k):
                                    captured_tokens[k] = v
                    except Exception:
                        pass

                if not captured_tokens.get("lsd"):
                    try:
                        full_html = await page.content()
                        fallback  = _parser.extract_tokens_from_html(full_html)
                        for k, v in fallback.items():
                            if v and not captured_tokens.get(k):
                                captured_tokens[k] = v
                    except Exception:
                        pass

                if not captured_tokens.get("device_id"):
                    captured_tokens["device_id"] = (
                        f"threads_{random.randint(10**15, 10**16 - 1)}"
                    )

                cookies = await context.cookies()

                self._lsd_token   = captured_tokens.get("lsd", "")
                self._fb_dtsg     = captured_tokens.get("fb_dtsg", "")
                self._device_id   = captured_tokens.get("device_id", "")
                self._app_id      = captured_tokens.get("app_id", self._app_id)
                self._cookies_str = cookies_to_header(cookies)

                print(
                    f"  Cookies: {len(cookies)} | lsd: {bool(self._lsd_token)} "
                    f"| fb_dtsg: {bool(self._fb_dtsg)}"
                )

                if not self._lsd_token:
                    print("  ERRO: lsd token não encontrado.")
                    try:
                        debug_path = INTERNAL_DIR / "debug_page.html"
                        full_html  = await page.content()
                        with open(debug_path, "w", encoding="utf-8") as f:
                            f.write(full_html)
                        print(
                            f"  HTML de debug salvo em {debug_path} "
                            f"({len(full_html)} chars)"
                        )
                    except Exception:
                        pass
                    return False

                if captured_doc_ids.get("search"):
                    self._doc_id_search = captured_doc_ids["search"]
                    print(f"  doc_id search descoberto: {self._doc_id_search}")
                if captured_doc_ids.get("user"):
                    self._doc_id_user = captured_doc_ids["user"]
                if captured_doc_ids.get("replies"):
                    self._doc_id_replies = captured_doc_ids["replies"]

                if self._doc_id_search:
                    self._save_doc_ids()
                else:
                    print(
                        "  AVISO: doc_id de busca não descoberto. "
                        "O Threads pode ter alterado a API."
                    )

                for resp_data in captured_search_data:
                    edges, _, _ = _parser.extract_search_edges(resp_data)
                    for edge in edges:
                        node = edge.get("node") or edge
                        post = _parser.parse_thread_item(node)
                        if post and post["url"] not in self.posts:
                            self.posts[post["url"]] = post
                if captured_search_data:
                    print(f"  Posts capturados via browser: {len(self.posts)}")

                self._save_session(cookies)
                return True

            except Exception as e:
                print(f"  Erro ao obter sessão: {e}")
                return False
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    async def _test_graphql(self) -> bool:
        """
        Testa se a sessão atual consegue fazer uma query GraphQL válida.

        Returns
        -------
        bool
            ``True`` se a query retornar ``"data"`` sem erros de doc_id.
        """
        if not self._doc_id_search:
            return False
        try:
            r = await asyncio.to_thread(
                graphql_request,
                self._doc_id_search,
                {"query": self._search_term, "count": 1},
                self._lsd_token,
                self._fb_dtsg,
                self._app_id,
                self.user_agent,
                self._cookies_str,
            )
            if r and "errors" in r:
                err_msg = r["errors"][0].get("message", "") if r["errors"] else ""
                if "not found" in err_msg.lower():
                    print(f"  doc_id expirado: {self._doc_id_search}")
                    self._doc_id_search = ""
                    return False
            return r is not None and "data" in r
        except Exception:
            return False

    def _graphql_request(
        self,
        doc_id:        str,
        variables:     dict,
        friendly_name: str = "",
        referer:       str = "",
    ) -> dict | None:
        """
        Executa uma query GraphQL usando as credenciais da sessão atual.

        Wrapper de conveniência sobre :func:`graphql.graphql_request` que
        injeta automaticamente ``lsd``, ``fb_dtsg``, ``app_id``,
        ``user_agent`` e ``cookies_str`` da instância.

        Parameters
        ----------
        doc_id : str
            Identificador da query GraphQL.
        variables : dict
            Variáveis da query.
        friendly_name : str, optional
            Nome amigável para o header ``X-FB-Friendly-Name``.
        referer : str, optional
            URL de referer para o header ``Referer``.

        Returns
        -------
        dict or None
            Resposta JSON da API, ou ``None`` em caso de erro.
        """
        return graphql_request(
            doc_id=doc_id,
            variables=variables,
            lsd_token=self._lsd_token,
            fb_dtsg=self._fb_dtsg,
            app_id=self._app_id,
            user_agent=self.user_agent,
            cookies_str=self._cookies_str,
            friendly_name=friendly_name,
            referer=referer,
        )

    # ------------------------------------------------------------------
    # Dados ocultos (hidden web data)
    # ------------------------------------------------------------------

    def _extract_hidden_data(self, html: str) -> None:
        """
        Extrai posts, cursor e tokens dos blocos ``<script data-sjs>``.

        Atualiza :attr:`posts`, :attr:`_latest_cursor`, :attr:`_lsd_token`
        e :attr:`_fb_dtsg` com os dados encontrados no HTML.

        Parameters
        ----------
        html : str
            HTML completo da página renderizada pelo browser.
        """
        new_posts, cursor, tokens = _parser.extract_hidden_data(html)
        for post in new_posts:
            if post["url"] not in self.posts:
                self.posts[post["url"]] = post
        if cursor:
            self._latest_cursor = cursor
        if tokens.get("lsd") and not self._lsd_token:
            self._lsd_token = tokens["lsd"]
        if tokens.get("fb_dtsg") and not self._fb_dtsg:
            self._fb_dtsg = tokens["fb_dtsg"]

    def _extract_posts_from_sjs(self, data) -> None:
        """
        Busca e adiciona posts dos dados SSR ao dicionário :attr:`posts`.

        Parameters
        ----------
        data : Any
            Dados JSON do bloco ``<script data-sjs>`` ou de uma resposta
            GraphQL intercedada pelo browser.
        """
        for post in _parser.extract_posts_from_sjs(data):
            if post["url"] not in self.posts:
                self.posts[post["url"]] = post

    def _process_graphql_response(self, data: dict) -> None:
        """
        Processa uma resposta GraphQL interceptada pelo browser.

        Extrai edges e thread_items, adicionando novos posts a
        :attr:`posts` e atualizando :attr:`_latest_cursor`.

        Parameters
        ----------
        data : dict
            Resposta JSON intercedada do endpoint ``/api/graphql``.
        """
        if not isinstance(data, dict) or "data" not in data:
            return
        edges, next_cursor, _ = _parser.extract_search_edges(data)
        for edge in edges:
            node = edge.get("node") or edge
            post = _parser.parse_thread_item(node)
            if post and post["url"] not in self.posts:
                self.posts[post["url"]] = post
        self._extract_posts_from_sjs(data)
        if next_cursor:
            self._latest_cursor = next_cursor
        else:
            c = _parser.find_any_cursor(data)
            if c:
                self._latest_cursor = c

    # ------------------------------------------------------------------
    # Busca de posts por keyword (Playwright + GraphQL)
    # ------------------------------------------------------------------

    async def search_posts(
        self,
        keyword: str,
        target:  int  = 200,
        fresh:   bool = False,
    ) -> list[str]:
        """
        Busca posts do Threads por keyword usando as abas mais abrangentes.

        O Threads não expõe busca via GraphQL sem autenticação, então o
        browser real é usado para navegar nas SERPs e extrair dados dos
        blocos ``<script data-sjs>`` (hidden web data). Visita apenas as
        abas de busca *mais abrangentes* da plataforma:

        1. ``serp_type=default`` — Top/Populares.
        2. ``serp_type=recent`` — Mais recentes (cronológico).
        3. ``serp_type=tags`` — Posts por hashtag relacionada.

        Não entra em perfis de usuários (escopo restrito à busca por termo)
        e não usa filtros de janela temporal restritivos (``past_day``,
        ``past_week``), que são subconjuntos do ``recent``.

        Após as abas, se o alvo não for atingido e a interceptação tiver
        capturado um contexto GraphQL válido, tenta paginação direta via
        HTTP GraphQL como fallback.

        Parameters
        ----------
        keyword : str
            Termo de busca recebido do GuaIA.
        target : int, optional
            Quantidade mínima de posts únicos desejada. Padrão: 200.
        fresh : bool, optional
            Se ``True``, descarta posts coletados em execuções anteriores
            antes de começar. Padrão: ``False``.

        Returns
        -------
        list[str]
            Lista de URLs únicas coletadas (até ``target`` itens).

        Notes
        -----
        Os feeds de busca do Threads são predominantemente SSR e não
        respondem a scroll programático sem que o app web dispare uma
        request GraphQL paginada. Por isso o número máximo de scrolls
        por aba é mantido baixo, evitando ciclos improdutivos.
        """
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] "
            f"Buscando '{keyword}' | Alvo: {target} posts"
            + (" | FRESH" if fresh else "")
            + "\n"
        )

        if fresh:
            self.posts.clear()

        async with async_playwright() as p:
            browser, context, page = await launch_stealth_browser(
                p, self.user_agent, self.headless
            )

            if COOKIES_FILE.exists():
                try:
                    with open(COOKIES_FILE, encoding="utf-8") as f:
                        saved_cookies = json.load(f)
                    await context.add_cookies(saved_cookies)
                except Exception:
                    saved_cookies = []
            else:
                saved_cookies = []

            if (
                THREADS_USERNAME
                and THREADS_PASSWORD
                and not has_auth_cookie(saved_cookies)
            ):
                login_ok = await perform_login(page, THREADS_USERNAME, THREADS_PASSWORD)
                if login_ok:
                    try:
                        cookies_now = await context.cookies()
                        self._save_session(cookies_now)
                        self._cookies_str = cookies_to_header(cookies_now)
                    except Exception:
                        pass

            graphql_responses: list[dict] = []
            graphql_response_count = {"n": 0}

            async def _on_response(resp):
                try:
                    if "/api/graphql" in resp.url:
                        ct = resp.headers.get("content-type", "")
                        if "json" in ct or "javascript" in ct:
                            graphql_response_count["n"] += 1
                            data = await resp.json()
                            if isinstance(data, dict):
                                graphql_responses.append(data)
                except Exception:
                    pass

            keyword_lower = (keyword or "").strip().lower()

            async def _on_request(req):
                """
                Captura requests GraphQL e identifica o contexto de busca.

                Estratégias (em ordem de prioridade):

                1. ``friendly_name`` contém ``"search"`` / ``"serp"``.
                2. Algum valor de variável contém a keyword buscada.
                3. Chave clássica (``"query"``, ``"searchWord"``, etc.).
                """
                try:
                    if req.method != "POST" or "/api/graphql" not in req.url:
                        return
                    import urllib.parse as _up
                    params   = _up.parse_qs(req.post_data or "")
                    doc_id   = (params.get("doc_id") or [None])[0]
                    vars_str = (params.get("variables") or [""])[0]
                    friendly = (
                        params.get("fb_api_req_friendly_name") or [""]
                    )[0]
                    if not doc_id or not vars_str:
                        return
                    try:
                        variables = json.loads(vars_str)
                    except Exception:
                        return
                    if not isinstance(variables, dict):
                        return

                    var_keys_preview = list(variables.keys())[:10]
                    print(
                        f"  [GQL-req] {friendly or '?'} | "
                        f"doc_id={doc_id[:10]}.. | keys={var_keys_preview}"
                    )

                    friendly_l   = (friendly or "").lower()
                    friendly_hit = any(
                        t in friendly_l
                        for t in ("search", "serp", "keyword", "tag")
                    )

                    value_hit = False
                    if keyword_lower:
                        def _scan(val) -> bool:
                            if isinstance(val, str):
                                return keyword_lower in val.lower()
                            if isinstance(val, (list, tuple)):
                                return any(_scan(v) for v in val)
                            if isinstance(val, dict):
                                return any(_scan(v) for v in val.values())
                            return False
                        value_hit = _scan(variables)

                    key_hit = any(
                        k in variables
                        for k in (
                            "query", "searchWord", "keyword",
                            "search_surface", "search_type", "q",
                            "text", "search_query", "searchQuery", "word",
                        )
                    )

                    if friendly_hit or value_hit or key_hit:
                        entry = {
                            "doc_id":        doc_id,
                            "variables":     variables,
                            "friendly_name": friendly,
                        }
                        self._all_captured_gql.append(entry)
                        has_after    = bool(variables.get("after"))
                        priority_new = (
                            (3 if value_hit else (2 if friendly_hit else 1))
                            + (10 if has_after else 0)
                        )
                        ctx          = self._search_gql_ctx
                        priority_cur = ctx.get("_priority", 0)
                        if priority_new >= priority_cur:
                            entry["_priority"]    = priority_new
                            self._search_gql_ctx  = entry
                            self._doc_id_search   = doc_id
                            print(
                                f"  [GraphQL] ✓ Capturado contexto busca "
                                f"(prio={priority_new}): "
                                f"friendly='{friendly}' "
                                f"keys={list(variables.keys())}"
                            )
                except Exception:
                    pass

            page.on("request", _on_request)
            page.on("response", _on_response)

            try:
                # Abas de busca mais abrangentes da plataforma:
                #   - default  → Top/Populares (resultado principal do Threads)
                #   - recent   → Mais recentes (cronológico, sem filtro temporal)
                #   - tags     → Posts associados a hashtags relacionadas
                #
                # Janelas temporais (past_day/past_week) não são usadas: são
                # subconjuntos restritivos do `recent` e não agregam alcance.
                # Perfis de usuários também não são visitados (fora do escopo
                # da busca por termo recebido do GuaIA).
                sources: list[tuple[str, str]] = [
                    (st, f"{BASE_URL}/search?q={quote(keyword)}&serp_type={st}")
                    for st in ("default", "recent", "tags")
                ]

                try:
                    await page.goto(
                        BASE_URL, wait_until="domcontentloaded", timeout=60_000
                    )
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(2.0, 3.5))
                except Exception:
                    pass

                viewport = page.viewport_size or {"width": 1366, "height": 768}
                mouse_x  = viewport["width"] // 2
                mouse_y  = viewport["height"] // 2
                try:
                    await page.mouse.move(mouse_x, mouse_y, steps=5)
                except Exception:
                    pass

                for serp_type, search_url in sources:
                    if len(self.posts) >= target:
                        break

                    print(f"  Navegando para {search_url}")
                    await page.goto(
                        search_url, wait_until="domcontentloaded", timeout=60_000
                    )
                    try:
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(3, 4))

                    try:
                        await page.mouse.move(mouse_x, mouse_y, steps=5)
                    except Exception:
                        pass

                    self._extract_hidden_data(await page.content())
                    for resp_data in graphql_responses:
                        self._process_graphql_response(resp_data)
                    graphql_responses.clear()

                    posts_before_tab = len(self.posts)
                    dom_count        = await count_dom_posts(page)
                    print(
                        f"  Aba '{serp_type}' | carga inicial: +{posts_before_tab}"
                        f" (parsed={len(self.posts)}, DOM={dom_count})"
                    )

                    # Scroll real raramente dispara nova paginação nas SERPs
                    # do Threads (são predominantemente SSR). Mantemos poucas
                    # iterações apenas para o caso de o app web disparar uma
                    # request GraphQL paginada que possa ser interceptada.
                    MAX_SCROLLS_PER_TAB = 5
                    MAX_STALE           = 3
                    stale_rounds        = 0

                    for scroll_round in range(1, MAX_SCROLLS_PER_TAB + 1):
                        if len(self.posts) >= target:
                            break
                        prev_count = len(self.posts)
                        prev_dom   = await count_dom_posts(page)

                        try:
                            for _ in range(random.randint(3, 5)):
                                await page.mouse.wheel(
                                    0, random.randint(900, 1400)
                                )
                                await asyncio.sleep(random.uniform(0.25, 0.55))
                        except Exception:
                            pass

                        try:
                            await page.evaluate("""
                                () => {
                                    window.scrollTo({
                                        top: document.body.scrollHeight || 9e6,
                                        behavior: 'instant',
                                    });
                                    document.querySelectorAll('*').forEach(el => {
                                        try {
                                            const s = getComputedStyle(el);
                                            if ((s.overflowY === 'auto' ||
                                                 s.overflowY === 'scroll') &&
                                                el.scrollHeight > el.clientHeight + 50) {
                                                el.scrollTop = el.scrollHeight;
                                            }
                                        } catch (e) {}
                                    });
                                    const sels = [
                                        '[data-pressable-container="true"]',
                                        'article',
                                        'div[role="article"]',
                                    ];
                                    for (const sel of sels) {
                                        const items = document.querySelectorAll(sel);
                                        if (items.length > 0) {
                                            items[items.length - 1]
                                                .scrollIntoView({ block: 'end' });
                                            break;
                                        }
                                    }
                                }
                            """)
                        except Exception:
                            pass

                        try:
                            await page.keyboard.press("End")
                            await page.keyboard.press("PageDown")
                        except Exception:
                            pass

                        try:
                            for txt in (
                                "Load more", "Ver mais",
                                "See more", "Show more", "Carregar mais",
                            ):
                                btn = page.get_by_role(
                                    "button", name=re.compile(txt, re.I)
                                )
                                if await btn.count() > 0:
                                    await btn.first.click(timeout=2000)
                                    await asyncio.sleep(random.uniform(1, 2))
                                    break
                        except Exception:
                            pass

                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=7_000
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(1.5, 2.8))

                        self._extract_hidden_data(await page.content())
                        for resp_data in graphql_responses:
                            self._process_graphql_response(resp_data)
                        graphql_responses.clear()

                        dom_count = await count_dom_posts(page)
                        gained    = len(self.posts) - prev_count
                        dom_gain  = dom_count - prev_dom
                        print(
                            f"  [{serp_type}] Scroll {scroll_round:03d} | "
                            f"+{gained:3d} novos | Total: {len(self.posts):4d} | "
                            f"DOM: {dom_count:4d} (Δ {dom_gain:+d})"
                        )

                        if gained == 0:
                            stale_rounds += 1
                            if stale_rounds >= MAX_STALE:
                                print(
                                    f"  Aba '{serp_type}' travou no scroll "
                                    f"({stale_rounds} rodadas sem novos, "
                                    f"{graphql_response_count['n']} GraphQL "
                                    f"resp. no total)."
                                )
                                break
                        else:
                            stale_rounds = 0

                        if scroll_round % 10 == 0:
                            delay = random.uniform(3, 6)
                            print(f"  Pausa anti-bloqueio: {delay:.1f}s …")
                            await asyncio.sleep(delay)

                    print(
                        f"  [diag] fim aba '{serp_type}': "
                        f"cursor_len={len(self._latest_cursor)}, "
                        f"gql_ctx_doc={bool(self._search_gql_ctx.get('doc_id'))}, "
                        f"candidatos_capturados={len(self._all_captured_gql)}"
                    )
                    if (
                        len(self.posts) < target
                        and (
                            self._search_gql_ctx.get("doc_id")
                            or self._all_captured_gql
                        )
                    ):
                        try:
                            self._cookies_str = cookies_to_header(
                                await context.cookies()
                            )
                            added = await self._paginate_via_graphql(
                                referer=search_url, target=target
                            )
                            print(
                                f"  [GraphQL] Paginação direta adicionou "
                                f"{added} posts."
                            )
                        except Exception as exc:
                            print(f"  [GraphQL] Erro na paginação direta: {exc}")

                    gained_this_tab = len(self.posts) - posts_before_tab
                    print(
                        f"  Aba '{serp_type}' finalizada "
                        f"(+{gained_this_tab} posts, total {len(self.posts)})"
                    )

                try:
                    cookies = await context.cookies()
                    self._save_session(cookies)
                except Exception:
                    pass

            except Exception as e:
                print(f"  Erro na coleta: {e}")
            finally:
                await browser.close()

        total = len(self.posts)
        print(f"\n  Total coletado: {total} posts únicos.\n")
        return list(self.posts.keys())[:target]

    async def _paginate_via_graphql(
        self,
        referer: str,
        target:  int,
    ) -> int:
        """
        Paginação direta via GraphQL HTTP — fallback quando o scroll do
        browser não dispara paginação em feeds virtualizados.

        Tenta múltiplos contextos capturados (ordenados por prioridade)
        até encontrar um que retorne novos posts.

        Parameters
        ----------
        referer : str
            URL de referer para os headers GraphQL.
        target : int
            Quantidade total de posts desejada.

        Returns
        -------
        int
            Número de posts novos adicionados.
        """
        if not self._lsd_token:
            print("  [GraphQL] Sem lsd token — pulando paginação direta.")
            return 0

        candidates = sorted(
            list(self._all_captured_gql),
            key=lambda e: e.get("_priority", 0),
            reverse=True,
        )
        seen_ids: set[str] = set()
        unique: list[dict] = []
        for c in candidates:
            did = c.get("doc_id")
            if did and did not in seen_ids:
                seen_ids.add(did)
                unique.append(c)

        if not unique and self._search_gql_ctx.get("doc_id"):
            unique = [self._search_gql_ctx]

        if not unique:
            print(
                "  [GraphQL] Nenhum contexto de busca capturado — "
                "pulando paginação direta."
            )
            return 0

        added_total = 0
        for c_idx, ctx in enumerate(unique, start=1):
            if len(self.posts) >= target:
                break
            doc_id    = ctx.get("doc_id")
            base_vars = dict(ctx.get("variables") or {})
            friendly  = ctx.get("friendly_name") or "BarcelonaSearchResultsQuery"
            cursor    = base_vars.get("after") or self._latest_cursor or ""

            print(
                f"  [GraphQL] Tentando candidato {c_idx}/{len(unique)}: "
                f"friendly='{friendly}', doc_id={doc_id[:10]}…, "
                f"vars_keys={list(base_vars.keys())}, "
                f"cursor_len={len(cursor)}"
            )

            page_i     = 0
            stale      = 0
            MAX_PAGES  = 60
            added_here = 0

            while len(self.posts) < target and page_i < MAX_PAGES:
                page_i += 1
                variables = dict(base_vars)
                if cursor:
                    variables["after"] = cursor
                if "first" not in variables and "count" not in variables:
                    variables["count"] = 15

                prev_total = len(self.posts)
                data = await asyncio.to_thread(
                    self._graphql_request,
                    doc_id, variables, friendly, referer,
                )

                if not data:
                    stale += 1
                    if stale >= 3:
                        print("  [GraphQL] Falhas consecutivas neste candidato.")
                        break
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                    continue

                if isinstance(data, dict) and data.get("errors"):
                    try:
                        err_msg = str(data["errors"])[:200]
                    except Exception:
                        err_msg = ""
                    print(f"  [GraphQL] Resposta com erros: {err_msg}")
                    break

                edges, next_cursor, has_next = _parser.extract_search_edges(data)
                for edge in edges:
                    node = edge.get("node") or edge
                    post = _parser.parse_thread_item(node)
                    if post and post["url"] not in self.posts:
                        self.posts[post["url"]] = post

                self._extract_posts_from_sjs(data)
                gained     = len(self.posts) - prev_total
                added_here += gained

                if not next_cursor:
                    next_cursor = _parser.find_any_cursor(data)

                print(
                    f"  [GraphQL c{c_idx}] Página {page_i:03d} | "
                    f"+{gained:3d} novos | "
                    f"Total: {len(self.posts):4d} | "
                    f"has_next={has_next} | cursor_len={len(next_cursor)}"
                )

                if gained == 0:
                    stale += 1
                    if stale >= 3:
                        print(
                            "  [GraphQL] Sem novos por 3 páginas — "
                            "próximo candidato."
                        )
                        break
                else:
                    stale               = 0
                    self._latest_cursor = next_cursor

                if not next_cursor:
                    print("  [GraphQL] Sem próximo cursor.")
                    break
                cursor = next_cursor

                await asyncio.sleep(random.uniform(1.2, 2.8))

            added_total += added_here
            if added_here > 0:
                print(
                    f"  [GraphQL] Candidato {c_idx} rendeu {added_here} posts."
                )

        return added_total

    # ------------------------------------------------------------------
    # Posts de um usuário específico
    # ------------------------------------------------------------------

    async def fetch_user_posts(
        self,
        username: str,
        target:   int = 100,
    ) -> list[str]:
        """
        Coleta posts de um usuário específico do Threads via GraphQL.

        Parameters
        ----------
        username : str
            Nome de usuário do Threads (sem o ``@``).
        target : int, optional
            Quantidade máxima de posts a coletar. Padrão: 100.

        Returns
        -------
        list[str]
            Lista de URLs únicas dos posts coletados.
        """
        print(f"\n  Coletando posts de @{username} …")

        ok = await self._obtain_session()
        if not ok:
            return []

        user_id = await self._resolve_user_id(username)
        if not user_id:
            print(f"  Não foi possível resolver user_id de @{username}")
            return []

        cursor    = ""
        page_num  = 0
        collected: dict[str, dict] = {}

        while len(collected) < target:
            page_num += 1
            variables: dict = {
                "userID": user_id,
                "count":  PAGE_SIZE,
                "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider": False,
            }
            if cursor:
                variables["after"] = cursor

            if not self._doc_id_user:
                print("  doc_id de perfil não disponível.")
                break

            data = await asyncio.to_thread(
                self._graphql_request,
                self._doc_id_user,
                variables,
                "BarcelonaProfileThreadsTabQuery",
                f"{BASE_URL}/@{username}",
            )

            if not data:
                break

            edges, next_cursor, has_next = _parser.extract_user_edges(data)
            gained = 0
            for edge in edges:
                node = edge.get("node") or edge
                post = _parser.parse_thread_item(node)
                if post and post["url"] not in collected:
                    collected[post["url"]] = post
                    self.posts[post["url"]] = post
                    gained += 1

            print(
                f"  Página {page_num:03d} | "
                f"+{gained:3d} novos | "
                f"Total: {len(collected):4d}"
            )

            if not has_next or not next_cursor or gained == 0:
                break

            cursor = next_cursor
            await asyncio.sleep(random.uniform(2.0, 4.0))

        return list(collected.keys())[:target]

    async def _resolve_user_id(self, username: str) -> str | None:
        """
        Resolve o ``user_id`` numérico a partir do username via API pública.

        Parameters
        ----------
        username : str
            Nome de usuário do Threads.

        Returns
        -------
        str or None
            ID numérico do usuário como string, ou ``None`` se não
            encontrado.
        """
        try:
            headers = build_headers(
                lsd_token=self._lsd_token,
                fb_dtsg=self._fb_dtsg,
                app_id=self._app_id,
                user_agent=self.user_agent,
                cookies_str=self._cookies_str,
                referer=f"{BASE_URL}/@{username}",
            )
            headers["Accept"] = "application/json"

            r = await asyncio.to_thread(
                lambda: requests.get(
                    f"https://www.threads.net/api/v1/users/web_profile_info/"
                    f"?username={username}",
                    headers=headers,
                    timeout=20,
                )
            )
            if r.status_code == 200:
                data = r.json()
                uid  = (
                    data.get("data", {}).get("user", {}).get("id")
                    or data.get("user", {}).get("pk")
                )
                if uid:
                    return str(uid)
        except Exception as e:
            print(f"    Erro ao resolver user_id: {e}")

        return None

    # ------------------------------------------------------------------
    # Comentários / Replies
    # ------------------------------------------------------------------

    async def fetch_comments(
        self,
        post_id:      str,
        code:         str = "",
        max_comments: int = 50,
    ) -> list[dict]:
        """
        Busca replies (comentários) de um post do Threads via GraphQL.

        No Threads, comentários são chamados de ``replies`` e têm a mesma
        estrutura de posts normais.

        Parameters
        ----------
        post_id : str
            ID numérico do post (campo ``pk`` na resposta GraphQL).
        code : str, optional
            Código alfanumérico do post (campo ``code``), usado para
            construir URLs de referer mais precisas.
        max_comments : int, optional
            Quantidade máxima de comentários a coletar. Padrão: 50.

        Returns
        -------
        list[dict]
            Lista de comentários no schema interno.
        """
        if not self._lsd_token:
            return []

        collected: dict[str, dict] = {}
        cursor      = ""
        page        = 0
        empty_pages = 0
        MAX_EMPTY   = 3

        while len(collected) < max_comments:
            page += 1
            variables: dict = {
                "postID": post_id,
                "count":  min(PAGE_SIZE, max_comments - len(collected)),
                "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider": False,
            }
            if cursor:
                variables["after"] = cursor
            if code:
                variables["mediaCode"] = code

            referer = (
                f"{BASE_URL}/t/{code}" if code else f"{BASE_URL}/t/{post_id}"
            )

            if not self._doc_id_replies:
                break

            data = await asyncio.to_thread(
                self._graphql_request,
                self._doc_id_replies,
                variables,
                "BarcelonaPostPageDirectRepliesQuery",
                referer,
            )

            if not data:
                empty_pages += 1
                if empty_pages >= MAX_EMPTY:
                    break
                await asyncio.sleep(random.uniform(1.0, 3.0))
                continue

            replies, next_cursor, has_next = _parser.extract_replies(data)
            gained = 0
            for reply in replies:
                c = _parser.parse_reply(reply)
                if c and c["comment_id"] not in collected:
                    collected[c["comment_id"]] = c
                    gained += 1

            if gained == 0:
                empty_pages += 1
                if empty_pages >= MAX_EMPTY:
                    break
            else:
                empty_pages = 0

            if not has_next or not next_cursor:
                break

            cursor = next_cursor
            await asyncio.sleep(random.uniform(0.5, 1.5))

        return list(collected.values())[:max_comments]

    async def fetch_comments_all(
        self,
        posts:        list[dict],
        max_per_post: int = 50,
        concurrency:  int = 3,
    ) -> None:
        """
        Busca comentários de múltiplos posts de forma concorrente.

        Embute os comentários em cada dict de post no campo ``_comments``.
        Posts sem ``post_id`` recebem ``_comments = []``.

        Parameters
        ----------
        posts : list[dict]
            Lista de posts no schema interno.
        max_per_post : int, optional
            Máximo de comentários por post. Padrão: 50.
        concurrency : int, optional
            Número máximo de coroutines simultâneas. Padrão: 3.
        """
        valid = [p for p in posts if p.get("post_id")]
        if not valid:
            for post in posts:
                post["_comments"] = []
            return

        semaphore   = asyncio.Semaphore(concurrency)
        total_posts = len(valid)
        print(
            f"\n  Coletando comentários de {total_posts} posts "
            f"(concorrência: {concurrency}, máx {max_per_post}/post) …\n"
        )

        async def _fetch_one(post: dict) -> None:
            post_id = post.get("post_id")
            code    = post.get("code", "")
            if not post_id:
                post["_comments"] = []
                return
            async with semaphore:
                try:
                    comments         = await self.fetch_comments(post_id, code, max_per_post)
                    post["_comments"] = comments
                    print(f"    {post_id}: {len(comments)} comentário(s)")
                except Exception as e:
                    print(f"    {post_id}: erro – {e}")
                    post["_comments"] = []

        await asyncio.gather(*[_fetch_one(p) for p in posts])

        total = sum(len(p.get("_comments") or []) for p in posts)
        print(f"\n  Total de comentários coletados: {total}\n")
