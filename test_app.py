import os, re, tempfile
d = tempfile.mkdtemp()
os.environ.update(DB_PATH=d + "/t.db", ADMIN_USER="rolo", ADMIN_PASSWORD="clave-de-prueba",
                  SECRET_KEY="x", INSECURE_COOKIES="1", BASE_URL="https://qr.ejemplo.com")
import app as A

c = A.app.test_client()
def tok(html): return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

# público
assert c.get("/").status_code == 302
assert c.get("/health").data == b"ok"
assert c.get("/RA001").status_code == 404           # no existe aún
assert c.get("/admin").status_code == 302            # sin sesión
assert c.get("/admin/tags").status_code == 302

# login malo / bueno
t = tok(c.get("/admin/login").text)
assert c.post("/admin/login", data={"user": "rolo", "password": "mal", "csrf": t}).status_code == 200
assert c.post("/admin/login", data={"user": "rolo", "password": "clave-de-prueba"}).status_code == 400  # sin csrf
r = c.post("/admin/login", data={"user": "rolo", "password": "clave-de-prueba", "csrf": t})
assert r.status_code == 302
t = tok(c.get("/admin").text)

# crear placas, cliente, asignar
assert c.post("/admin/tags/new", data={"start": 1, "count": 3, "plate": "PVC", "csrf": t}).status_code == 302
assert b"RA003" in c.get("/admin/tags").data
c.post("/admin/clients", data={"name": "Lerodent", "phone": "0981", "csrf": t})
assert b"Lerodent" in c.get("/admin/clients").data
page = c.get("/admin/tags/RA001").text
assert "https://qr.ejemplo.com/RA001?s=n" in page
r = c.post("/admin/tags/RA001", data={"client_id": 1, "dest_url": "ChIJabcdefghijk123", "plate": "PVC", "active": "on", "csrf": t})
assert r.status_code == 302

# redirección + conteo
r = c.get("/RA001?s=n"); assert r.status_code == 302
assert r.headers["Location"] == "https://search.google.com/local/writereview?placeid=ChIJabcdefghijk123"
assert c.get("/ra001?s=q").status_code == 302       # minúsculas
# sin activar
r = c.get("/RA002"); assert r.status_code == 200 and "todavía no está activada" in r.text
# pausada
c.post("/admin/tags/RA001", data={"client_id": 1, "dest_url": "https://ejemplo.com", "plate": "PVC", "csrf": t})
assert c.get("/RA001").status_code == 200
# destino inválido
c.post("/admin/tags/RA002", data={"dest_url": "javascript:alert(1)", "plate": "PVC", "active": "on", "csrf": t})
assert c.get("/RA002").status_code == 200
# ids raros
for bad in ["RA", "RAxyz", "admin2", "RA1234567"]:
    assert c.get("/" + bad).status_code == 404, bad

# reporte, qr, pedidos
rep = c.get("/admin/report").text
assert "Lerodent" in rep
assert c.get("/admin/report.csv").text.startswith("cliente,placa,escaneos")
png = c.get("/admin/qr/RA001.png"); assert png.data[:4] == b"\x89PNG"
c.post("/admin/orders", data={"client_id": 1, "items": "2 placas", "total": "180.000", "csrf": t})
assert "180.000" in c.get("/admin/orders").text
assert c.get("/admin/report?mes=hola").status_code == 400
assert c.post("/admin/logout", data={"csrf": t}).status_code == 302
assert c.get("/admin").status_code == 302
print("TODO OK")
