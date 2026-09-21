# UNED Backup Scraper

Aplicación local para Windows 10 que crea una copia de seguridad de los
materiales que una sesión propia puede abrir en Campus UNED/Ágora.

Lo he hecho porque no me atrae mucho como está hecha la web de la universidad (Cada profesor tiene su propio orden, estructura y te tienes que adaptar a cada uno), por eso prefiero descargarlo, tenerlo todo estructurado y normalizado en local. Por ello hice un proyecto modular para extraer todos los contenidos de cada asignatura con poco más que tus credenciales y un par de clicks.

La versión 1.9.0 descarga archivos, recorre todas las asignaturas visibles y
conserva como PDF el contenido que Moodle solo publica como página web:
tareas, cuestionarios, revisiones de intentos, vistas generales y detalles de
Open Grader.

> [!IMPORTANT]
> Este proyecto no está afiliado, respaldado ni mantenido por la UNED. Úsalo
> únicamente con tu propia cuenta, respetando las condiciones del servicio y
> los derechos de autor de los materiales. No publiques la copia generada.

## Seguridad y privacidad

- El programa nunca solicita ni almacena usuario o contraseña.
- El inicio de sesión se realiza manualmente en una ventana real del navegador.
- No intenta eludir autenticación, permisos, CAPTCHA ni segundo factor.
- Solo realiza navegación de lectura y descarga contenido que la sesión puede abrir.
- `.gitignore` excluye perfiles, cookies, copias, logs, manifiestos y configuración local.

Antes de publicar cambios, ejecuta `git status` y comprueba que no aparece
`.browser-profile`, `UNED_Backup`, `manifest.json`, `inspection.json`,
`download_log.txt` ni `config.json`.

## Instalación rápida en Windows 10

Requiere Python 3.11 o superior y, preferiblemente, Google Chrome.

1. Descarga o clona este repositorio.
2. Abre `uned-backup-scraper/scripts/windows/`.
3. Ejecuta `instalar.bat`.
4. Ejecuta `ejecutar_todo.bat`.
5. Cuando se abra Ágora, inicia sesión manualmente y sigue los mensajes de la consola.

El instalador crea `config.json` a partir de `config.example.json` sin
sobrescribir una configuración existente.

## Scripts de Windows

| Script | Función |
| --- | --- |
| `instalar.bat` | Crea `.venv`, instala el paquete y Playwright. |
| `ejecutar_todo.bat` | Inspecciona las asignaturas y realiza la copia. |
| `solo_inspeccionar.bat` | Regenera únicamente `inspection.json`. |
| `solo_descargar.bat` | Reutiliza la inspección y actualiza la copia. |
| `diagnostico.bat` | Comprueba Python, configuración y dependencias. |

## Uso desde terminal

```powershell
cd uned-backup-scraper
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config.example.json config.json
.venv\Scripts\python.exe -m uned_backup all --config config.json
```

Comandos disponibles:

```text
uned-backup inspect
uned-backup backup
uned-backup all
uned-backup doctor
```

## Resultado local

```text
uned-backup-scraper/
├── UNED_Backup/
│   ├── Asignatura 1/
│   └── Asignatura 2/
├── inspection.json
├── manifest.json
├── download_log.txt
└── UNED_Backup_AAAA-MM-DD.zip
```

Estos archivos se ignoran en Git de forma deliberada.

## Cómo funciona

1. Abre Ágora con un perfil local persistente de Playwright.
2. Consulta el listado autenticado de cursos y conserva como respaldo los enlaces del DOM.
3. Abre cada curso y consulta su índice interno de actividades cuando está disponible.
4. Recorre las secciones sin seguir duplicados del selector de idioma.
5. Descarga adjuntos y archivos de entrega accesibles.
6. Imprime a PDF las páginas sin descarga directa, incluidas las revisiones paginadas.
7. Evita duplicados por URL y SHA-256.
8. Genera un manifiesto y un ZIP final.

La arquitectura se explica en [`docs/architecture.md`](./docs/architecture.md).

## Desarrollo

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m unittest discover -s tests -v
```

Las pruebas se ejecutan también en GitHub Actions para Python 3.11 y 3.12.

## Limitaciones

- Los enlaces rotos en el propio curso seguirán devolviendo 404.
- No se descargan vídeos o audios para evitar copias enormes.
- Algunos recursos externos pueden bloquear descargas automatizadas.
- Solo se conserva aquello que la cuenta autenticada puede visualizar.

## Licencia

MIT. Consulta la licencia situada en la raíz del repositorio.
