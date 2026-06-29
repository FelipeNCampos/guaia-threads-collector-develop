"""
Configurações e constantes do extrator de metadados do Threads.

Centraliza variáveis de ambiente, caminhos de arquivo, URLs,
doc_ids padrão, user agents e o script de stealth do Playwright.
"""

import os
import random
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers de ambiente
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    """
    Lê uma variável de ambiente como booleano.

    Parameters
    ----------
    key : str
        Nome da variável de ambiente.
    default : bool
        Valor padrão caso a variável não esteja definida ou seja inválida.

    Returns
    -------
    bool
        ``True`` para valores ``"1"``, ``"true"`` ou ``"yes"``;
        ``False`` para ``"0"``, ``"false"`` ou ``"no"``;
        ``default`` para qualquer outro valor.
    """
    v = os.getenv(key, "")
    if v.lower() in ("1", "true", "yes"):
        return True
    if v.lower() in ("0", "false", "no"):
        return False
    return default


def _env_int(key: str, default: int) -> int:
    """
    Lê uma variável de ambiente como inteiro.

    Parameters
    ----------
    key : str
        Nome da variável de ambiente.
    default : int
        Valor padrão caso a variável não esteja definida ou seja inválida.

    Returns
    -------
    int
        Valor inteiro lido, ou ``default`` em caso de falha.
    """
    try:
        return int(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Ambiente de execucao
# ---------------------------------------------------------------------------

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_DEV = APP_ENV == "development"
MEDIA_LOCAL_PATH = os.getenv("MEDIA_LOCAL_PATH", "./media").strip()


# ---------------------------------------------------------------------------
# User Agents
# ---------------------------------------------------------------------------

USER_AGENTS: list[str] = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def random_user_agent() -> str:
    """
    Retorna um User-Agent aleatório da lista ``USER_AGENTS``.

    Returns
    -------
    str
        String de User-Agent selecionada aleatoriamente.
    """
    return random.choice(USER_AGENTS)


# ---------------------------------------------------------------------------
# Caminhos de arquivo
# ---------------------------------------------------------------------------

INTERNAL_DIR: Path = Path("data")
INTERNAL_DIR.mkdir(exist_ok=True)

COOKIES_FILE: Path = INTERNAL_DIR / "threads_cookies.json"
TOKENS_FILE:  Path = INTERNAL_DIR / "threads_tokens.json"
DOC_IDS_FILE: Path = INTERNAL_DIR / "threads_doc_ids.json"

# ---------------------------------------------------------------------------
# URLs base
# ---------------------------------------------------------------------------

#: Threads migrou de threads.net para threads.com
BASE_URL:    str = "https://www.threads.com"
GRAPHQL_URL: str = "https://www.threads.com/api/graphql"

# ---------------------------------------------------------------------------
# doc_ids padrão do GraphQL
# ---------------------------------------------------------------------------

#: Estes valores são descobertos dinamicamente via Playwright interceptando
#: os requests do app web. Os defaults abaixo são fallbacks que podem
#: ficar desatualizados quando o Meta atualiza o Threads.
DOC_ID_SEARCH:        str = ""
DOC_ID_USER_THREADS:  str = ""
DOC_ID_THREAD_DETAIL: str = ""
DOC_ID_REPLIES:       str = ""

# ---------------------------------------------------------------------------
# Parâmetros de coleta
# ---------------------------------------------------------------------------

#: Threads limita a ~10 resultados por página na busca
PAGE_SIZE: int = 10

# ---------------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------------

THREADS_USERNAME: str = os.getenv("THREADS_USERNAME", "").strip()
THREADS_PASSWORD: str = os.getenv("THREADS_PASSWORD", "")

# ---------------------------------------------------------------------------
# Script de stealth para Playwright
# ---------------------------------------------------------------------------

#: Remove propriedades que denunciam o browser como automatizado.
STEALTH_SCRIPT: str = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en']
    });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    window.chrome = {
        runtime: {},
        loadTimes: function(){},
        csi: function(){},
        app: {}
    };
    delete window.__playwright;
    delete window.__pw_manual;
    delete window.playwright;
})();
"""
