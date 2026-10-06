# Document tools

Refactorización de los scripts personales de captura, recorte, creación de PDF y OCR. Configuración por argumentos; sin cuentas, cookies, URL de un libro específico ni rutas personales.

## Instalación

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

OCR requiere además Tesseract instalado en el sistema y el idioma `spa` (o el indicado por `--language`).

## Procesar imágenes locales

```bash
python process.py pdf ./pages ./output/document.pdf
python process.py pdf ./pages ./output/cropped.pdf --crop 100,80,1800,1000
python process.py ocr ./pages ./output/document.txt --language spa
```

Si Tesseract no está en PATH: `--tesseract "C:/Program Files/Tesseract-OCR/tesseract.exe"`.

Los archivos se ordenan naturalmente: página 2 precede a página 10. El recorte debe caber en cada imagen. No sobrescribe salidas existentes. Un error de OCR o escritura puede dejar una salida parcial: se informa y se devuelve error; hay que revisarla antes de usarla.

## Captura opcional

```bash
python capture.py "https://example.org/document" ./pages --pages 5 --next-selector "button.next"
```

Abre un navegador nuevo y espera un inicio de sesión manual, si procede. No guarda credenciales ni reutiliza perfiles. Requiere un selector único que avance una página por clic; adaptar a la interfaz autorizada. El contador representa capturas, no garantiza correspondencia con páginas impresas. La detección de capturas idénticas es una comprobación básica, no garantiza renderizado completo.

Usar con documentos propios o cuyo acceso y reproducción estén autorizados. No incluye mecanismos para sortear restricciones del proveedor. Se elimina la variante CDP específica del navegador personal y la llamada inexistente `browser.disconnect()`.

## Verificación

Tres pruebas locales de orden, recorte y entrada vacía; sintaxis y ayuda CLI comprobadas. Captura autenticada, conversión PDF y motor OCR no ejecutados en esta revisión. Las imágenes de entrada son recursos del usuario, no archivos de código faltantes.
