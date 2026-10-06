# Token profile radar

Filtro de metadatos públicos de perfiles de Solana, derivado del radar personal original. Solo consulta; no usa claves, no conecta una cartera y no compra ni vende.

```bash
python radar.py             # una consulta
python radar.py --watch --interval 60
```

Solo requiere la biblioteca estándar de Python. Filtra por icono, cabecera, enlaces Twitter/Telegram y longitud de descripción. **Estos metadatos no demuestran legitimidad, liquidez ni seguridad de un token.** Se han eliminado esas afirmaciones del original.

El modo continuo recuerda solo los perfiles de la respuesta anterior: un perfil puede reaparecer si desaparece y vuelve. El endpoint externo no se ha probado en esta revisión; se comprobaron la sintaxis, ayuda y filtro con datos sintéticos.
