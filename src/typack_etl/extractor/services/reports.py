import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# URL base de la API
BASE_URL = "https://mts2.mtsolutions.io/api"

def _create_session(retries=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504)):
    """
    Crea una sesión de requests con reintentos para GET/POST.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,  # reintentar en estos códigos
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def get_sabana_report(token, start, end, devid=None):
    """
    Consulta el endpoint de sabana con rango de fechas y opcionalmente un devid (línea).
    Retorna lista de objetos JSON con los datos.
    """
    session = _create_session()
    headers = {"Authorization": f"JWT {token}"}
    params = {
        "report": "sabana",
        "start": start,
        "end": end
    }

    if devid:  # si se especifica, filtrar por máquina
        params["devid"] = devid

    url = f"{BASE_URL}/reports"
    resp = session.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()
