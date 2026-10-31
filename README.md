# Redireccionador NFC/QR · RA Digital

Cada placa tiene un ID (RA001, RA002…). El chip y el QR llevan `https://qr.TUDOMINIO.com/RA001`.
El servicio cuenta el escaneo y manda al destino actual. Se cambia el destino desde el panel, sin regrabar.

## Panel (`/admin`)
- **Placas:** crear IDs en lote, asignar cliente y destino (link o Place ID de Google), pausar, bajar QR en PNG.
- **Clientes y pedidos:** quién compró qué, total y si pagó.
- **Reporte:** escaneos por mes y por local (NFC vs QR), con CSV.
- Placa sin destino o pausada: muestra "todavía no está activada" con botón a tu WhatsApp.

## Desplegar en Coolify (DigitalOcean)
1. Subir esta carpeta a un repositorio (GitHub) y crear una app **Dockerfile** en Coolify.
2. Variables de entorno: copiar `.env.example` y completar `SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD`, `BASE_URL`.
3. **Volumen persistente** montado en `/data` (ahí vive la base SQLite). Sin esto se pierden los datos.
4. Dominio: `qr.TUDOMINIO.com` apuntando al servidor, con HTTPS automático. Puerto interno: 8000.
5. Probar `https://qr.TUDOMINIO.com/health` → `ok`.
6. Hacer copia de `/data/qr.db` seguido (backup de Coolify o `sqlite3 qr.db ".backup copia.db"`).

## Uso diario
1. Panel → Placas → crear RA001–RA100 (una sola vez).
2. Al entregar una placa: abrir su ID → elegir cliente → pegar link de reseñas o Place ID → Guardar.
3. Grabar en el chip con NFC Tools: link de la ficha de la placa terminado en `?s=n` (`?s=q` en el QR). Así el reporte separa NFC y QR.
4. Probar tocando la placa con el celular.

## Probar local
```
pip install -r requirements.txt
ADMIN_PASSWORD=algo INSECURE_COOKIES=1 python app.py   # http://localhost:8000/admin
python test_app.py                                     # pruebas
```

## Notas
- Redirección 302 (no 301) para que los cambios de destino se vean al instante.
- No se guarda IP, solo fecha, vía y navegador del escaneo.
- Las horas del panel están en UTC (Paraguay es UTC-3).
