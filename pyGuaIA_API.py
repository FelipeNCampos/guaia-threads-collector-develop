## Library to connect to and use the GuaIA's Backend API
## Author: Hugo Nascimento

import os
import requests
import yaml
from pathlib import Path
import json

class GuaIA_API:
  
  version=1 
  api_url=''
  token=''
  returncode=0
  reason=''
  
  def __init__(self, filename):
     print("Loading config...")
     self.loadConfig(filename)
     print("Connecting to server...")
     if not self.connect():
        print(f"  Connection failed! Code '{self.returncode}', Reason '{self.reason}'")
     return None
   
     
  def getLastReturnCode(self):
     return self.returncode
   
  def getLastReturnReason(self):
     return self.reason
   
  def loadConfig(self, filename):
     if not os.path.exists(filename):
       print(f"File '{filename}' does not exist.\n")
       exit(0)
     
     cfg = yaml.safe_load(Path(filename).read_text())
     self.api_url=cfg['API_URL']
     self.email=cfg['EMAIL']
     self.password=cfg['PASSWORD']
     self.tenant=cfg['TENANT_ID']
     
     return True
 
 
  def connect(self):
     ret = False
     if self.version==1:
         ret = self.connect_old()
         if not ret:
            self.version=2
     if not ret and self.version==2:
         ret = self.connect_new()
         
     if ret: print("...Connected!")
     else: print("...Not connected!")
     
     return ret
       
       
  def connect_old(self):
    api_endpoint = self.api_url+"/v3/auth/local"
    api_data = {'email':self.email, 'password':self.password, 'tenantId': self.tenant}
    
    r=requests.post(url=api_endpoint, data=api_data)
    self.returncode = r.status_code
    self.reason = r.reason
    result = r.json()
      
    ret=False  
    if self.returncode == 200 and 'access_token' in result: 
      ret=True
      self.token=result['access_token']
    else: self.token=''
    
    return ret
    
    
  def connect_new(self):
    api_endpoint = self.api_url+"/v3/auth/login"
    api_data = {'email':self.email, 'password':self.password, 'tenantId': self.tenant}
    
    r=requests.post(url=api_endpoint, data=api_data)
    self.returncode = r.status_code
    self.reason = r.reason
    result = r.json()
    
    ret=False  
    if self.returncode == 200 and 'accessToken' in result: 
      ret=True
      self.token=result['accessToken']
    else: 
      self.token=''
    
    return ret
  
  
  def getUnEvaluatedPublications(self):
    api_endpoint = self.api_url+"/v3/ai/publication/non-evaluated" # /publications/ai/non-evaluated
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    elif  self.returncode == 404:  # not found
       return True, []
       
    return False, []


  def setPublicationAIAssessment(self, id_publication=-1, assessment_type=None, ia_code=None, ia_name=None,
           classification_score=0, explanation_text=None, extracted_text=None, highlighted_text=None, 
           detailed_analysis_text=None, attribute_where=None, attribute_who=None, attribute_what=None,
           attribute_when=None, attribute_keywords=None, attribute_logic=None, attribute_pseudolabel=None,
           distortion_potential=None, missinformation_potential=None):
    api_endpoint = self.api_url+"/v3/ai/publication/evaluate" # /publications/ai/evaluate
    api_headers = {"Authorization": f"Bearer {self.token}"}
    api_data = {'id_publication':id_publication, 'assessment_type':assessment_type, 'ia_code':ia_code, 'ia_name':ia_name, 
                'classification_score':classification_score, 'explanation_text':explanation_text, 'extracted_text':extracted_text, 'highlighted_text':highlighted_text, 
                'detailed_analysis_text':detailed_analysis_text, 'attribute_where':attribute_where, 'attribute_who':attribute_who, 'attribute_what':attribute_what, 
                'attribute_when':attribute_when, 'attribute_keywords':attribute_keywords, 'attribute_logic':attribute_logic, 'attribute_pseudolabel':attribute_pseudolabel,
                'distortion_potential':distortion_potential, 'missinformation_potential':missinformation_potential}
  
    r=requests.post(url=api_endpoint, headers=api_headers, data=api_data)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    return self.returncode == 201, self.returncode
  
  
  ## Busca a avaliação da IA de uma ou de todas as publicações.
  ##Input: O parâmetro id_publication é opcional. Caso ele não seja passado, 
  ##          a busca retorna a avaliação da IA de todas as publicações.
  def getPublicationAIAssessment(self, id_publication=None):
    if id_publication is None:
       id_txt=''
    else: id_txt="/"+str(id_publication)
    api_endpoint = self.api_url+f"/v3/ai/publication/evaluation{id_txt}" 
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    elif  self.returncode == 404:  # not found
       return True, []
       
    return False, []
  
  
  ## Apaga a avaliação da IA de uma ou de todas as publicações.
  ##Input: O parâmetro id_publication é opcional. Caso ele não seja passado, 
  ##          a rotina apaga a avaliação da IA de todas as publicações.
  def deletePublicationAIAssessment(self, id_publication=None):
    if id_publication is None:
       id_txt=''
    else: id_txt="/"+str(id_publication)
    api_endpoint = self.api_url+f"/v3/ai/publication/evaluation{id_txt}" 
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.delete(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, self.returncode
    elif  self.returncode == 404:  # not found
       return True, self.returncode
       
    return False, self.returncode
  
  
   ## Media type can be None-Todas, 1-texto, 2-imagem, 3-audio, 4-video   
  def getUnEvaluatedMedia(self, mediatype=None):
    if mediatype is not None and (mediatype < 1 or mediatype > 4):
       print(f"Undefined media type '{mediatype}'.\n")
       return False, []
    
    if mediatype is None: 
       api_endpoint = self.api_url+f"/v3/ai/media/non-evaluated?"
    else:
       api_endpoint = self.api_url+f"/v3/ai/media/non-evaluated?media_type={mediatype}"
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    elif  self.returncode == 404:  # not found
       return True, []
    
    return False, []
    
  ##########################################################  
  # Cadastro da avaliação de uma mídia feita por uma IA 
  ##########################################################
  #  id_media: INT 
  #  assessment_type: INT  -- can be: 
  #     1 - Classification
  #     2 - Attribute extraction
  #     3 - Distortion detection
  #     4 - Text Transcription
  #     5 - Text Transcription from Audio
  #     6 - Text Transcrição from Video
  ##  ia_code: INT
  ##  ia_name: String
  ##  classification_score: REAL -- representando o nível de confiança da classe abaixo
  ##  class_value: INT --  com 0=verdadeira e 1=false
  ##  explanation_text: String
  ##  description: String -- Descrição da mídia feita pela IA. Pode ter uma análise mais complexa da mídia do que o campo extracted_text, já que este último é apenas um mera transcrição do áudia da mídia
  ##  extracted_text: String
  ##  highlighted_text: String
  ##  detailed_analysis_text: String
  ##  attribute_where: String
  ##  attribute_who: String
  ##  attribute_what: String
  ##  attribute_when: String
  ##  distortion_level: Real [0,1] -- Nível de distorção fornecido pela IA
  ##  file_obj_name: String: nome do arquivo de distortion heatmap ou outro similar
  ##  file_obj: arquivo de distortion heatmap
  ##  file_obj_type: String -- definição do tipo de arquivo, como 'image/jpeg'
  def setMediaAIAssessment(self, id_media=-1, assessment_type=0, ia_code=0, ia_name='', 
                           classification_score=None, class_value=None, explanation_text=None, 
                           description=None, extracted_text=None, highlighted_text=None, 
                           detailed_analysis_text=None, attribute_where=None, attribute_who=None, 
                           attribute_what=None, attribute_when=None, attribute_keywords=None, 
                           attribute_logic=None, attribute_pseudolabel=None, distortion_level=None, 
                           priority=0, show_ia_result1_text=None, show_ia_result1_comment=None,
                           show_ia_result2_text=None, show_ia_result2_comment=None, fields_to_show=None, 
                           distortion_potential=None, missinformation_potential=None,
                           file_obj_name=None, file_obj=None, file_obj_type=None):
    api_endpoint = self.api_url+"/v3/ai/media/evaluate"
    api_headers = {"Authorization": f"Bearer {self.token}"} #, 'content-type': "multipart/form-data"}
    api_data = {'id_media':id_media, 'assessment_type':assessment_type, 'ia_code':ia_code, 'ia_name':ia_name, 
                'classification_score':classification_score, 'class':class_value, 'explanation_text':explanation_text, 
                'description':description,  'extracted_text':extracted_text, 'highlighted_text':highlighted_text, 
                'detailed_analysis_text':detailed_analysis_text, 'attribute_where':attribute_where, 'attribute_who':attribute_who, 
                'attribute_what':attribute_what, 'attribute_when':attribute_when, 'attribute_keywords':attribute_keywords, 
                'attribute_logic':attribute_logic, 'attribute_pseudolabel':attribute_pseudolabel, 'distortion_level':distortion_level,
                'priority':priority,'show_ia_result1_text':show_ia_result1_text, 'show_ia_result1_comment':show_ia_result1_comment, 
                'show_ia_result2_text':show_ia_result2_text, 'show_ia_result2_comment':show_ia_result2_comment,
                'fields_to_show':fields_to_show, 
                'distortion_potential':distortion_potential, 'missinformation_potential':missinformation_potential}
    #api_files = {'file': distortion_heatmap}
    if file_obj is not None:
       api_files = {'files': (file_obj_name, file_obj, file_obj_type)}
    else: api_files = {}
    #api_files = {'files': ("heatmap.jpg", distortion_heatmap,'multipart/form-data',{'Expires': '0'})}
    
    try:
       r=requests.post(url=api_endpoint, headers=api_headers, data=api_data, files=api_files)
       print(f"Código de retorno: {r.status_code}")
    except requests.exceptions.HTTPError as errh:
       print(f"Erro HTTP: {errh}")
    except requests.exceptions.ConnectionError as errc:
       print(f"Erro de conexão: {errc}")
    except requests.exceptions.Timeout as errt:
       print(f"Erro de tempo limite: {errt}")
    except requests.exceptions.RequestException as err:
       print(f"Erro inesperado: {err}") 
    
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    return self.returncode == 201, self.returncode
    
  
  ## Busca a avaliação da IA de uma ou de todas as mídias.
  ##Input: O parâmetro id_publication é opcional. Caso ele não seja passado, 
  ##          a busca retorna a avaliação da IA de todas as mídias de todas as publicações.
  def getMediaAIAssessment(self, id_publication=None, id_media=None, assessment_type=None):
    param_txt=""
    if (id_publication is not None) and (id_publication!=""):
       param_txt="?id_publication="+str(id_publication)
    if (id_media is not None) and (id_media != ""):
       if param_txt == "":
          param_txt="?id_media="+str(id_media)
       else:  param_txt=param_txt+"&id_media="+str(id_media)
    if (assessment_type is not None) and (assessment_type != ""):
       if param_txt == "":
          param_txt="?assessment_type="+str(assessment_type)
       else:  param_txt=param_txt+"&assessment_type="+str(assessment_type)
       
    api_endpoint = self.api_url+f"/v3/ai/media/evaluation{param_txt}" 
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    elif  self.returncode == 404:  # not found
       return True, []
       
    return False, []
  
  
  ## Apaga a avaliação da IA de uma ou de todas as Mídias.
  ##Input: O parâmetro id_publication é opcional. Caso ele não seja passado, 
  ##          a rotina apaga a avaliação da IA de todas as mídias de todas as publicações.
  def deleteMediaAIAssessment(self, id_publication=None, id_media=None, assessment_type=None):
    param_txt=""
    if (id_publication is not None) and (id_publication!=""):
       param_txt="?id_publication="+str(id_publication)
    if (id_media is not None) and (id_media != ""):
       if param_txt == "":
          param_txt="?id_media="+str(id_media)
       else:  param_txt=param_txt+"&id_media="+str(id_media)
    if (assessment_type is not None) and (assessment_type != ""):
       if param_txt == "":
          param_txt="?assessment_type="+str(assessment_type)
       else:  param_txt=param_txt+"&assessment_type="+str(assessment_type)
    api_endpoint = self.api_url+f"/v3/ai/media/evaluation{param_txt}"
    api_headers = {"Authorization": f"Bearer {self.token}"}
                
    r=requests.delete(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, self.returncode
    elif  self.returncode == 404:  # not found
       return True, self.returncode
       
    return False, self.returncode


#################################################################################################
### Funcões antigas com regras de negócio específicas das IA, a serem substituídas
#################################################################################################

  def setPublicationAssessment(self, id_publication=-1, assessment_type=0, ia_code=0, ia_name='', 
                              classification_score=0, explanation_text='', extracted_text='', highlighted_text='', 
                              detailed_analysis_text='', attribute_where='', attribute_who='', attribute_what='', 
                              attribute_when='', attribute_keywords='', attribute_logic='', attribute_pseudolabel=0):
     print("Warning: Function setPublicationAssessment is going to be removed. Use function setPublicationAIAssessment instead.") 
     # regra definida para a apresentação da avaliação de IA para texto
     assessment_type=2
     if classification_score<=0.5:
        missinformation_potential=0.0
     else:
        missinformation_potential=2.0 * (classification_score - 0.5)
     ret, code = self.setPublicationAIAssessment(id_publication=id_publication, assessment_type=assessment_type, ia_code=ia_code, ia_name=ia_name, 
                                                 classification_score=classification_score, explanation_text=explanation_text, 
                                                 extracted_text=extracted_text, highlighted_text=highlighted_text, 
                                                 detailed_analysis_text=detailed_analysis_text, attribute_where=attribute_where, 
                                                 attribute_who=attribute_who, attribute_what=attribute_what, 
                                                 attribute_when=attribute_when, attribute_keywords=attribute_keywords, 
                                                 attribute_logic=attribute_logic, attribute_pseudolabel=attribute_pseudolabel,
                                                 distortion_potential=None, missinformation_potential=missinformation_potential)
     return ret, code 


  def setImageAIAssessment(self, id_media=-1, assessment_type=0, ia_code=0, ia_name="", classification_score=None, class_value=None, distortion_level=None, 
                           distortion_heatmap_filename=None, distortion_heatmap=None):   
    print("Warning: Function setImageAIAssessment is going to be removed. Use function setMediaAIAssessment instead.") 
    ## regra definida para a apresentação da avaliação de IA para imagem
    assessment_type=3
    if (class_value == 1) and (distortion_level >= 0.9):
       show_ia_result1_text="Conteúdo com alto potencial de distorção por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(distortion_level*100):.2f}".replace('.', ',')+"%"
    elif (class_value == 1) and (distortion_level < 0.9):
       show_ia_result1_text="Conteúdo com baixo potencial de distorção por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(classification_score*100):.2f}".replace('.', ',')+"%"
    else:
       show_ia_result1_text="Não foram identificados indícios relevantes de distorções geradas por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(classification_score*100):.2f}".replace('.', ',')+"%"
    show_ia_result1_comment="O conteúdo com potencial de distorção pode ser gerado por IA ou manipulado por humano."
    distortion_potential=distortion_level
    ret, code = self.setMediaAIAssessment(id_media=id_media, assessment_type=assessment_type, ia_code=ia_code, ia_name=ia_name, 
                                          classification_score=classification_score, class_value=class_value, distortion_level=distortion_level, 
                                          file_obj_name=distortion_heatmap_filename, file_obj=distortion_heatmap, file_obj_type='image/jpeg',
                                          show_ia_result1_text=show_ia_result1_text, show_ia_result1_comment=show_ia_result1_comment, 
                                          show_ia_result2_text=show_ia_result2_text, distortion_potential=distortion_potential)
    return ret, code
    
   
  def setAudioAIAssessment(self, id_media=-1, assessment_type=0, ia_code=0, ia_name="", classification_score=None, class_value=None, 
                           distortion_level=None, explanation_text=None, extracted_text=None, highlighted_text=None, detailed_analysis_text=None, 
                           attribute_where=None, attribute_who=None, attribute_what=None, attribute_when=None, attribute_keywords=None, 
                           attribute_logic=None, attribute_pseudolabel=None):
    print("Warning: Function setAudioAIAssessment is going to be removed. Use function setMediaAIAssessment instead.") 
    # regra definida para a apresentação da avaliação de IA para áudio
    assessment_type=3
    show_ia_result1_text="Análise da geração do áudio: " + ("áudio gerado por humano." if class_value==0 else "áudio sintético.")
    show_ia_result2_text="Probabilidade do resultado da análise: " + f"{(classification_score*100):.2f}".replace('.', ',')+"%"
    distortion_potential=distortion_level
    ret, code = self.setMediaAIAssessment(id_media=id_media, assessment_type=assessment_type, ia_code=ia_code, ia_name=ia_name, 
                  classification_score=classification_score, class_value=class_value, distortion_level=distortion_level, 
                  explanation_text=explanation_text, extracted_text=extracted_text, highlighted_text=highlighted_text, 
                  detailed_analysis_text=detailed_analysis_text, attribute_where=attribute_where, attribute_who=attribute_who, 
                  attribute_what=attribute_what, attribute_when=attribute_when, attribute_keywords=attribute_keywords, 
                  attribute_logic=attribute_logic, attribute_pseudolabel=attribute_pseudolabel,
                  show_ia_result1_text=show_ia_result1_text, show_ia_result2_text=show_ia_result2_text, 
                  distortion_potential=distortion_potential)
    return ret, code    


  def setVideoAIAssessment_old(self, id_media=-1, assessment_type=0, ia_code=0, ia_name="", classification_score=None, class_value=None, 
                               distortion_level=None, description=None):
    print("Warning: Function setVideoAIAssessment_old is going to be removed. Use function setMediaAIAssessment instead.") 
    ## regra definida para a apresentação da avaliação antiga de IA para vídeo
    assessment_type=1
    if (class_value == 1) and (distortion_level >= 0.9):
       show_ia_result1_text="Conteúdo com alto potencial de distorção por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(distortion_level*100):.2f}".replace('.', ',')+"%"
    elif (class_value == 1) and (distortion_level < 0.9):
       show_ia_result1_text="Conteúdo com baixo potencial de distorção por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(classification_score*100):.2f}".replace('.', ',')+"%"
    else:
       show_ia_result1_text="Não foram identificados indícios relevantes de distorções geradas por IA."
       show_ia_result2_text="Nível de confiança da IA: " + f"{(classification_score*100):.2f}".replace('.', ',')+"%"
    show_ia_result1_comment="O conteúdo com potencial de distorção pode ser gerado por IA ou manipulado por humano."
    distortion_potential=distortion_level

    ret, code = self.setMediaAIAssessment(id_media=id_media, assessment_type=assessment_type, ia_code=ia_code, ia_name=ia_name, 
                                          classification_score=classification_score, class_value=class_value, distortion_level=distortion_level, 
                                          description=description, show_ia_result1_text=show_ia_result1_text, show_ia_result1_comment=show_ia_result1_comment, 
                                          show_ia_result2_text=show_ia_result2_text, distortion_potential=distortion_potential)
    return ret, code       
  

  def setVideoAIAssessment_iacode42(self, id_media=-1, assessment_type=0, ia_code=0, ia_name="", classification_score=None, class_value=None,
                                  distortion_level=None, explanation_text=None, extracted_text=None, detailed_analysis_text=None, 
                                  attribute_where=None, attribute_who=None, attribute_what=None, attribute_when=None, attribute_keywords=None, 
                                 attribute_logic=None, attribute_pseudolabel=None, description=None):
    
    print("Warning: Function setVideoAIAssessment_iacode42 is going to be removed. Use function setMediaAIAssessment instead.")
    ## regra definida para a apresentação da avaliação nova de IA para vídeo
    assessment_type=2
    if (class_value==1) and (classification_score>0.5):
        missinformation_potential=2.0 * (classification_score - 0.5)
    else:
        missinformation_potential=0.0   

    distortion_potential=distortion_level
    if description is not None:
       fields_to_show="'description':'Descrição: '"
    else: fields_to_show=None
    ret, code = self.setMediaAIAssessment(id_media=id_media, assessment_type=assessment_type, ia_code=ia_code, ia_name=ia_name, 
                  classification_score=classification_score, class_value=class_value, distortion_level=distortion_level, 
                  description=description, explanation_text=explanation_text, extracted_text=extracted_text, 
                  detailed_analysis_text=detailed_analysis_text, attribute_where=attribute_where, attribute_who=attribute_who, 
                  attribute_what=attribute_what, attribute_when=attribute_when, attribute_keywords=attribute_keywords, 
                  attribute_logic=attribute_logic, attribute_pseudolabel=attribute_pseudolabel, fields_to_show=fields_to_show,
                  missinformation_potential=missinformation_potential, 
                  distortion_potential=distortion_potential)
    return ret, code       


#################################################################################################
### Rotinas para gestão de monitoramento
#################################################################################################

  # Return a string with the name of a social network, given a associated number
  def socialnetwork_name(self, num):
     if num==1: return "Reddit"
     elif num==2: return "X"
     elif num==3: return "YouTube"
     else: return "Other"


  ## Retorna as consultas pendentes.
  ## Input: O parâmetro socialnetwork é um número que indica a rede social (1 - Reddit, 2 - X [Antigo Twitter], 3 - YouTube). 
  ## Caso ele não seja passado, a consulta retornará as consultas pendentes de todas as redes
  def getUnprocessedQueries(self, socialnetwork=None):
    param_txt=""
    if (socialnetwork is not None) and (socialnetwork!=""):
       param_txt="?query_source="+str(socialnetwork)
    api_endpoint = self.api_url+f"/v3/monitoring/pending{param_txt}" 
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    #elif  self.returncode == 404:  # not found
    #   return True, []
       
    return False, []
  

  ## Retorna as consultas cadastradas.
  ## Input: 
  ##    + id_query -- Opcional 'None' ou o id de uma query. Se for passado 'None', então todos os Ids são considerados para inclusão na resposta.
  ##    + socialnetwork -- O parâmetro socialnetwork é um número que indica a rede social.
  def getQuery(self, id_query=None, socialnetwork=None):
    param_txt=""
    if (id_query is not None) and (id_query!=""):
       param_txt="?id_query="+str(id_query)
    if (socialnetwork is not None) and (socialnetwork!=""):
       param_txt=param_txt+"?query_source="+str(socialnetwork)
    api_endpoint = self.api_url+f"/v3/monitoring/query{param_txt}" 
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    #print (api_endpoint)
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()
    #elif  self.returncode == 404:  # not found
    #   return True, []
       
    return False, []

 
  ## Atualiza o status da busca
  ## Input:
  ##    + id_query -- Campo obrigatório com o Id da consulta cujo estado tentará ser mudado
  ##    + status -- valor do estado 2:"Em Execução", "3: Concluído", 4:"Concluído com falta",
  ##                5:"Falhou" e 6:"Cancelado"
  def setQueryStatus(self, id_query, status):
    if status==2: # Em Execução
       api_endpoint = self.api_url+"/v3/monitoring/start"
    elif status==3: # Concluído
       api_endpoint = self.api_url+"/v3/monitoring/finish"
    elif status==4: # Concluído com falta
       api_endpoint = self.api_url+"/v3/monitoring/complete-but-fail"
    elif status==5: # Falhou
       api_endpoint = self.api_url+"/v3/monitoring/fail"
    elif status==6: # Cancelado
       api_endpoint = self.api_url+"/v3/monitoring/cancel"
    else:   
       api_endpoint = self.api_url+"/v3/monitoring/cancel"
    api_endpoint=api_endpoint+"/"+str(id_query)
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    r=requests.post(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    return self.returncode == 200, self.returncode


  ## Envia uma postagem de resultado para uma consulta
  ## Input: 
  ##    + id_query *: number -- ID da coleta associada a esta publicação.
  ##    + publication_date *: string($date-time) -- Data e hora da publicação na rede.
  ##    + publication_text: string -- Texto da publicação - OPCIONAL caso exista mídia cadastrada
  ##    + source_type: number -- Fonte de onde a publicação foi extraída (escolha única entre: 0 - Outro, 1 - Site/Blog, 2 - Instagram, 3 - Facebook, 4 - WhatsApp, 5 - X (antigo Twitter), 6 - Tiktok, 7 - Threads, 8 - Grupo de Facebook, 9 - Grupo de WhatsApp, 10 - Telegram, 11-Reddit, 12-YouTube)
  ##    + other_source_name: string -- Descrição textual da fonte da publicação, quando id_source_type for outro (0).
  ##    + primary_source_name *: string -- Nome de quem publicou a informação pela primeira vez e onde ela foi publicada.
  ##    + publication_url:string -- URL da publicação
  ##    + allegation *: string -- Alegação do usuário
  ##    + comment: string -- Comentários adicionais relacionados à publicação.
  ##    + observation: string -- Observações adicionais sobre a publicação.
  ##    + id_target_type: array -- Alvo da publicação. Deve ser um array de números (1 - Justiça Eleitoral, 2 - Processo Eleitoral, 3 - Autoridade (Juiz Eleitoral), 4 - Candidato Político, 5 - Governante (prefeito, governador, vereador)
  ##    + social_metadata: object -- Metadados específicos da rede social (JSON). Quando fornecido, é gravado em extraction.publication.social_metadata.
  ##    Os últimos três campos ainda não são utilizados
  def sendQuerySingleResult(self, id_query=None, publication_date=None, publication_text="", source_type=0, other_source_name="", primary_source_name="", publication_url="", allegation="", comment="", observation="", id_target_type=[], social_metadata={}, file_obj_name="", file_obj=None, file_obj_type=""):
    api_endpoint = self.api_url+"/v3/monitoring/publication"
    api_headers = {"Authorization": f"Bearer {self.token}"} #, 'Content-Type': "multipart/form-data"}
    api_data = {'id_query':id_query, 
                'publication_date':publication_date, 
                'publication_text':publication_text,
                'source_type':source_type, 
                'other_source_name':other_source_name,  
                'primary_source_name':primary_source_name,
                'publication_url':publication_url,
                'allegation':allegation, 
                'comment':comment, 
                'observation':observation,
                'id_target_type':id_target_type, 
                'social_metadata':json.dumps(social_metadata)}
    if file_obj is not None:
       api_files = {'files': (file_obj_name, file_obj, file_obj_type)}
    else: api_files = {}
    
    #print("endpoint=", api_endpoint)
    #print("headers=", api_headers)
    #print("data=", api_data)
    try:
       r=requests.post(url=api_endpoint, headers=api_headers, data=api_data, files=api_files)
       print(f"Código de retorno: {r.status_code}")
    except requests.exceptions.HTTPError as errh:
       print(f"Erro HTTP: {errh}")
    except requests.exceptions.ConnectionError as errc:
       print(f"Erro de conexão: {errc}")
    except requests.exceptions.Timeout as errt:
       print(f"Erro de tempo limite: {errt}")
    except requests.exceptions.RequestException as err:
       print(f"Erro inesperado: {err}") 
    
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    return self.returncode == 201, self.returncode


  ## Retorna os resultados/postagens de uma consulta.
  ## Input: 
  ##    + id_query -- id da consulta.
  def getQueryResults(self, id_query):
    api_endpoint = self.api_url+f"/v3/monitoring/query/{id_query}/publications"
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    #print (api_endpoint)
    r=requests.get(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j = r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    if self.returncode == 200: 
       return True, r.json()['publications']
    #elif  self.returncode == 404:  # not found
    #   return True, []
       
    return False, []
    
    
  ## Apaga um resultado/postagem (de uma consulta).
  ## Input: 
  ##    + id_publication -- id da postagem.  
  def deleteResult(self, id_publication):
    api_endpoint = self.api_url+f"/v3/monitoring/publication/{id_publication}"
    api_headers = {"Authorization": f"Bearer {self.token}"}
    
    #print (api_endpoint)
    r=requests.delete(url=api_endpoint, headers=api_headers)
    self.returncode = r.status_code
    j =r.json()
    if 'message' in j:
      self.reason = j['message']
    else: self.reason = r.reason
    
    return self.returncode == 200, self.returncode
    
  
  ## Apaga todos os resultados/postagens de uma consulta.
  ## Input: 
  ##    + id_query -- id da consulta.
  def deleteQueryResults(self, id_query):
    ret, rlist = self.getQueryResults(id_query)
    if not ret:
      return ret, self.getLastReturnCode()
    
    for r in rlist:
      id_publication=r['id_publication']
      print(f"Postagem {id_publication} excluída.")
      ret, code = self.deleteResult(id_publication)
      if not ret:
         return ret, code
     
    return True, 200
