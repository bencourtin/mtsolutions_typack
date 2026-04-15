import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import API_USER, API_PASS

# URL base de la API
BASE_URL = "https://mts2.mtsolutions.io/api"

def _create_session(retries=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504)):
    """
    Crea una sesión de requests con politica de reintentos para resiliencia en fallos temporales.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,  # códigos HTTP que se deben reintentar
        allowed_methods=["POST"],  # método que usaremos aquí
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def get_token():
    """
    Obtiene el token JWT de autenticación usando credenciales de settings.
    """
    session = _create_session()
    url = f"{BASE_URL}/login"
    payload = {
        "login": {
            "user": API_USER,
            "password": API_PASS,
            "locale": "es_CL"  # configuración regional según documentación
        }
    }

    # Intentar autenticación
    response = session.post(url, json=payload, timeout=30)
    response.raise_for_status()  # lanza excepción en caso de error HTTP
    data = response.json()
    return data["token"]
