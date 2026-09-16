import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# URL base de la API
BASE_URL = "https://mts2.mtsolutions.io/api"

# Códigos que justifican reintentar: fallas transitorias del servidor y el
# rate limit. 429 respeta la cabecera Retry-After si la API la envía.
STATUS_REINTENTABLES = (429, 500, 502, 503, 504)


class TokenExpirado(Exception):
    """La API rechazó el token. Hay que renovarlo y volver a intentar."""


def _create_session(retries=3, backoff_factor=0.5, status_forcelist=STATUS_REINTENTABLES):
    """
    Crea una sesión de requests con reintentos para GET/POST.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,  # reintentar en estos códigos
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True,
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

    Lanza `TokenExpirado` si la API responde 401 o 403, para que quien llame
    pueda renovar el token en lugar de perder la línea. Un lote de 11 líneas
    puede cruzar la expiración del token a mitad de camino.
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
    if resp.status_code in (401, 403):
        raise TokenExpirado(f"La API rechazó el token (HTTP {resp.status_code})")
    resp.raise_for_status()
    return resp.json()
