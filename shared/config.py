import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://crawler:crawler@localhost:5432/crawler_db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Job defaults
DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_URLS = 50000
DEFAULT_CONCURRENT_REQUESTS = 32
DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN = 8
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# SEO thresholds
TITLE_MIN_LEN = 10
TITLE_MAX_LEN = 60
DESCRIPTION_MIN_LEN = 50
DESCRIPTION_MAX_LEN = 160

# Google corta el titulo y la descripcion del resultado por PIXELES, no por
# caracteres: un titulo de 65 letras estrechas cabe y uno de 55 en mayusculas
# no. Medido en tres censos, 4.637 + 3.634 + 530 titulos salian como
# "demasiado largos" por caracteres sin pasar del limite de pixeles, o sea sin
# truncarse en el resultado: el 29% de los avisos de titulo era falso. Al
# contrario solo pasaba en 3 paginas de 80.000.
# ~580 px es el ancho del titulo en escritorio y ~985 px el del fragmento de
# descripcion (dos lineas), que es lo que usa Screaming Frog.
TITLE_MAX_PIXELS = 580
DESCRIPTION_MAX_PIXELS = 985
