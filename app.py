"""
Cementerio General de Guacotecti · Control de títulos y espacios (versión web)

Servidor Flask + PostgreSQL pensado para Railway.

Variables de entorno:
  DATABASE_URL   (obligatoria) conexión a PostgreSQL. En Railway: ${{Postgres.DATABASE_URL}}
  ADMIN_USUARIO  usuario administrador inicial (por defecto: admin)
  ADMIN_CLAVE    clave del administrador inicial; solo se usa si todavía no hay usuarios
  SECRET_KEY     (opcional) clave para firmar las sesiones
  ZONA_HORARIA   (opcional) diferencia con UTC en horas, por defecto -6 (El Salvador)
"""
import gzip
import hashlib
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg
from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
ADMIN_USUARIO = (os.environ.get('ADMIN_USUARIO') or 'admin').strip().lower()
ADMIN_CLAVE = os.environ.get('ADMIN_CLAVE') or ''
TZ = timezone(timedelta(hours=float(os.environ.get('ZONA_HORARIA', '-6'))))
MAX_RESPALDOS = 40
VERSION = '1.6.2'
ROLES = ('admin', 'editor', 'lectura')
ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,40}$')
USER_RE = re.compile(r'^[a-z0-9._\-]{3,30}$')

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY') or hashlib.sha256(('cementerio-guacotecti|' + DATABASE_URL).encode()).hexdigest(),
    SESSION_COOKIE_NAME='cementerio',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
)


def ahora_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------- base de datos
def conectar():
    if not DATABASE_URL:
        raise RuntimeError('Falta la variable DATABASE_URL')
    return psycopg.connect(DATABASE_URL)


def db():
    if 'db' not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def cerrar_db(exc):
    c = g.pop('db', None)
    if c is not None:
        try:
            if exc is not None:
                c.rollback()
            c.close()
        except Exception:
            pass


def q(sql, params=(), uno=False, todos=False):
    cur = db().cursor()
    cur.execute(sql, params)
    if uno:
        return cur.fetchone()
    if todos:
        return cur.fetchall()
    return cur


ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
  id BIGSERIAL PRIMARY KEY,
  usuario TEXT UNIQUE NOT NULL,
  nombre TEXT NOT NULL DEFAULT '',
  clave TEXT NOT NULL,
  rol TEXT NOT NULL DEFAULT 'editor',
  activo SMALLINT NOT NULL DEFAULT 1,
  cambiar SMALLINT NOT NULL DEFAULT 0,
  sv BIGINT NOT NULL DEFAULT 0,
  creado BIGINT NOT NULL DEFAULT 0
);
CREATE SEQUENCE IF NOT EXISTS cambios_seq;
CREATE TABLE IF NOT EXISTS titulos (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  version BIGINT NOT NULL,
  borrado SMALLINT NOT NULL DEFAULT 0,
  modificado BIGINT NOT NULL DEFAULT 0,
  modificado_por TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS titulos_version ON titulos(version);
CREATE TABLE IF NOT EXISTS config (
  clave TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  version BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS respaldos (
  id BIGSERIAL PRIMARY KEY,
  fecha BIGINT NOT NULL,
  motivo TEXT NOT NULL,
  usuario TEXT NOT NULL DEFAULT '',
  titulos INTEGER NOT NULL DEFAULT 0,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bitacora (
  id BIGSERIAL PRIMARY KEY,
  fecha BIGINT NOT NULL,
  usuario TEXT NOT NULL,
  accion TEXT NOT NULL,
  titulo_id TEXT NOT NULL DEFAULT '',
  detalle TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS bitacora_titulo ON bitacora(titulo_id);
CREATE TABLE IF NOT EXISTS formularios (
  id TEXT PRIMARY KEY,
  numero TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL,
  version BIGINT NOT NULL,
  borrado SMALLINT NOT NULL DEFAULT 0,
  creado BIGINT NOT NULL DEFAULT 0,
  modificado BIGINT NOT NULL DEFAULT 0,
  modificado_por TEXT NOT NULL DEFAULT ''
);
"""

_init_lock = threading.Lock()
_init_ok = False


def inicializar():
    """Crea las tablas y el administrador inicial. Se reintenta hasta que la base responda."""
    global _init_ok
    if _init_ok:
        return
    with _init_lock:
        if _init_ok:
            return
        c = conectar()
        try:
            cur = c.cursor()
            cur.execute('SELECT pg_advisory_xact_lock(424242)')
            for stmt in [s.strip() for s in ESQUEMA.split(';') if s.strip()]:
                cur.execute(stmt)
            cur.execute('SELECT COUNT(*) FROM usuarios')
            if int(cur.fetchone()[0]) == 0 and ADMIN_CLAVE:
                cur.execute('INSERT INTO usuarios (usuario, nombre, clave, rol, cambiar, creado) VALUES (%s,%s,%s,%s,1,%s)',
                            (ADMIN_USUARIO, 'Administrador', generate_password_hash(ADMIN_CLAVE), 'admin', ahora_ms()))
            c.commit()
            _init_ok = True
        finally:
            c.close()


@app.before_request
def antes():
    if request.path in ('/salud',) or request.path.startswith('/static/'):
        return None
    try:
        inicializar()
    except Exception as e:  # base de datos aún no disponible
        if request.path.startswith('/api/'):
            return jsonify(error='sin_base', mensaje='No se pudo conectar con la base de datos. ' + str(e)[:200]), 503
    # Protección contra envíos desde otros sitios
    if request.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('X-Requested-With') != 'cementerio':
            return jsonify(error='solicitud_no_valida'), 400
    return None


@app.after_request
def despues(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'same-origin'
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    # compresión de respuestas grandes
    if (resp.status_code == 200 and not resp.direct_passthrough and 'gzip' in request.headers.get('Accept-Encoding', '')
            and resp.mimetype in ('application/json', 'text/html') and 'Content-Encoding' not in resp.headers):
        datos = resp.get_data()
        if len(datos) > 20000:
            resp.set_data(gzip.compress(datos, 6))
            resp.headers['Content-Encoding'] = 'gzip'
            resp.headers['Vary'] = 'Accept-Encoding'
    return resp


# ---------------------------------------------------------------- sesión y permisos
def usuario_actual():
    if 'u' not in g:
        g.u = None
        uid, sv = session.get('uid'), session.get('sv')
        if uid is not None:
            row = q('SELECT id, usuario, nombre, rol, activo, cambiar, sv FROM usuarios WHERE id=%s', (uid,), uno=True)
            if row and int(row[4]) == 1 and int(row[6]) == int(sv or 0):
                g.u = {'id': int(row[0]), 'usuario': row[1], 'nombre': row[2] or row[1], 'rol': row[3], 'cambiar': int(row[5]) == 1}
    return g.u


def requiere(*roles):
    def deco(f):
        @wraps(f)
        def envoltura(*a, **k):
            u = usuario_actual()
            if not u:
                return jsonify(error='no_autenticado'), 401
            if u['cambiar']:
                return jsonify(error='cambiar_clave', mensaje='Debe cambiar su clave antes de continuar.'), 403
            if roles and u['rol'] not in roles:
                return jsonify(error='sin_permiso', mensaje='Su usuario no tiene permiso para esta acción.'), 403
            return f(*a, **k)
        return envoltura
    return deco


EDITA = ('admin', 'editor')

_fallos = {}
_fallos_lock = threading.Lock()


def bloqueado(clave):
    with _fallos_lock:
        lst = [t for t in _fallos.get(clave, []) if t > time.time() - 900]
        _fallos[clave] = lst
        return len(lst) >= 8


def registrar_fallo(clave):
    with _fallos_lock:
        _fallos.setdefault(clave, []).append(time.time())


def bitacora(accion, titulo_id='', detalle=''):
    u = usuario_actual()
    q('INSERT INTO bitacora (fecha, usuario, accion, titulo_id, detalle) VALUES (%s,%s,%s,%s,%s)',
      (ahora_ms(), u['nombre'] if u else '', accion, titulo_id or '', detalle[:500]))


def _ver_tupla(v):
    try:
        return tuple(int(x) for x in v.split('.'))
    except Exception:
        return (0,)


def pagina_mas_reciente():
    """Busca la página del sistema (index.html, o copias como 'index (1).html') en la carpeta static
    y en la raíz, y usa la de versión más nueva. Así no importa dónde ni con qué nombre se subió."""
    mejor = (None, None, (-1,), -1)
    for carpeta in (os.path.join(BASE_DIR, 'static'), BASE_DIR):
        try:
            nombres = os.listdir(carpeta)
        except OSError:
            continue
        for n in nombres:
            if not n.lower().endswith('.html'):
                continue
            ruta = os.path.join(carpeta, n)
            try:
                with open(ruta, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except OSError:
                continue
            m = re.search(r"const APP_VERSION='([0-9.]+)'", txt)
            if not m and 'CEMENTERIO' not in txt.upper():
                continue
            ver = _ver_tupla(m.group(1)) if m else (0,)
            mt = os.path.getmtime(ruta)
            if (ver, mt) > (mejor[2], mejor[3]):
                mejor = (carpeta, n, ver, mt)
    return mejor[0], mejor[1]


# ---------------------------------------------------------------- páginas
@app.route('/')
def inicio():
    carpeta, nombre = pagina_mas_reciente()
    if not nombre:
        return Response('No se encontró index.html en el repositorio de GitHub.', 500, mimetype='text/plain; charset=utf-8')
    resp = send_from_directory(carpeta, nombre)
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                               "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
    return resp


@app.route('/salud')
def salud():
    return 'ok'


# ---------------------------------------------------------------- autenticación
@app.get('/api/estado')
def estado():
    hay = int(q('SELECT COUNT(*) FROM usuarios', uno=True)[0])
    return jsonify(usuarios=hay > 0, adminConfigurado=bool(ADMIN_CLAVE), version=VERSION)


@app.post('/api/entrar')
def entrar():
    d = request.get_json(silent=True) or {}
    usuario = str(d.get('usuario', '')).strip().lower()
    clave = str(d.get('clave', ''))
    llave = (request.remote_addr or '') + '|' + usuario
    if bloqueado(llave):
        return jsonify(error='bloqueado', mensaje='Demasiados intentos. Espere 15 minutos e intente de nuevo.'), 429
    row = q('SELECT id, clave, activo, sv FROM usuarios WHERE usuario=%s', (usuario,), uno=True)
    if not row or int(row[2]) != 1 or not check_password_hash(row[1], clave):
        registrar_fallo(llave)
        time.sleep(0.6)
        return jsonify(error='credenciales', mensaje='Usuario o clave incorrectos.'), 401
    session.clear()
    session.permanent = True
    session['uid'] = int(row[0])
    session['sv'] = int(row[3])
    g.pop('u', None)
    bitacora('entró al sistema')
    db().commit()
    return yo()


@app.post('/api/salir')
def salir():
    session.clear()
    return jsonify(ok=True)


@app.get('/api/yo')
def yo():
    u = usuario_actual()
    if not u:
        return jsonify(error='no_autenticado'), 401
    vacio = int(q('SELECT COUNT(*) FROM titulos WHERE borrado=0', uno=True)[0]) == 0
    return jsonify(usuario=u['usuario'], nombre=u['nombre'], rol=u['rol'], cambiar=u['cambiar'], sinDatos=vacio, version=VERSION)


@app.post('/api/clave')
def cambiar_clave():
    u = usuario_actual()
    if not u:
        return jsonify(error='no_autenticado'), 401
    d = request.get_json(silent=True) or {}
    actual, nueva = str(d.get('actual', '')), str(d.get('nueva', ''))
    row = q('SELECT clave, sv FROM usuarios WHERE id=%s', (u['id'],), uno=True)
    if not check_password_hash(row[0], actual):
        return jsonify(error='credenciales', mensaje='La clave actual no es correcta.'), 400
    if len(nueva) < 8:
        return jsonify(error='clave_corta', mensaje='La clave nueva debe tener al menos 8 caracteres.'), 400
    if nueva == actual:
        return jsonify(error='clave_igual', mensaje='La clave nueva debe ser distinta de la actual.'), 400
    sv = int(row[1]) + 1
    q('UPDATE usuarios SET clave=%s, cambiar=0, sv=%s WHERE id=%s', (generate_password_hash(nueva), sv, u['id']))
    session['sv'] = sv
    bitacora('cambió su clave')
    db().commit()
    g.pop('u', None)
    return yo()


# ---------------------------------------------------------------- datos
def seq_actual():
    r = q('SELECT COALESCE(MAX(version),0) FROM titulos', uno=True)
    c = q("SELECT COALESCE(MAX(version),0) FROM config", uno=True)
    return max(int(r[0]), int(c[0]))


def leer_reporte():
    return leer_config('reporte')


def leer_config(clave):
    row = q("SELECT data, version FROM config WHERE clave=%s", (clave,), uno=True)
    return (json.loads(row[0]), int(row[1])) if row else ({}, 0)


def guardar_config(clave, data):
    ver = int(q("SELECT nextval('cambios_seq')", uno=True)[0])
    q("INSERT INTO config (clave, data, version) VALUES (%s,%s,%s) ON CONFLICT (clave) DO UPDATE SET data=EXCLUDED.data, version=EXCLUDED.version",
      (clave, json.dumps(data, ensure_ascii=False, separators=(',', ':')), ver))
    return ver


@app.get('/api/datos')
@requiere()
def datos():
    seq = seq_actual()
    rows = q('SELECT id, data, version, modificado_por FROM titulos WHERE borrado=0', todos=True)
    titulos, versiones = {}, {}
    for rid, data, ver, por in rows:
        titulos[rid] = json.loads(data)
        versiones[rid] = int(ver)
    rep, _ = leer_reporte()
    croq, _ = leer_config('croquis')
    fondo, _ = leer_config('croquis_fondo')
    return jsonify(titulos=titulos, versiones=versiones, reporte=rep, croquis=croq, croquisFondo=fondo.get('img'), seq=seq)


@app.get('/api/cambios')
@requiere()
def cambios():
    try:
        desde = int(request.args.get('desde', '0'))
    except ValueError:
        desde = 0
    seq = seq_actual()
    rows = q('SELECT id, data, version, borrado FROM titulos WHERE version>%s', (desde,), todos=True)
    titulos, versiones, borrados = {}, {}, []
    for rid, data, ver, bor in rows:
        if int(bor):
            borrados.append(rid)
        else:
            titulos[rid] = json.loads(data)
            versiones[rid] = int(ver)
    out = dict(titulos=titulos, versiones=versiones, borrados=borrados, seq=seq)
    rep, rv = leer_reporte()
    if rv > desde:
        out['reporte'] = rep
    croq, cv = leer_config('croquis')
    if cv > desde:
        out['croquis'] = croq
    fondo, fv = leer_config('croquis_fondo')
    if fv > desde:
        out['croquisFondo'] = fondo.get('img')
    out['version'] = VERSION
    return jsonify(out)


def respaldo_diario():
    """Antes del primer cambio de cada día guarda una copia completa."""
    hoy = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_ms = int(hoy.timestamp() * 1000)
    r = q("SELECT COUNT(*) FROM respaldos WHERE motivo='diario' AND fecha>=%s", (inicio_ms,), uno=True)
    if int(r[0]) == 0 and int(q('SELECT COUNT(*) FROM titulos WHERE borrado=0', uno=True)[0]) > 0:
        crear_respaldo('diario')


def todo_json():
    rows = q('SELECT id, data FROM titulos WHERE borrado=0 ORDER BY id', todos=True)
    rep, _ = leer_reporte()
    croq, _ = leer_config('croquis')
    fondo, _ = leer_config('croquis_fondo')
    croq = dict(croq)
    croq['fondo'] = fondo.get('img')
    frows = q('SELECT id, numero, data, creado, modificado, modificado_por FROM formularios WHERE borrado=0 ORDER BY creado', todos=True)
    forms = [{'id': r[0], 'numero': r[1], 'datos': json.loads(r[2]), 'creado': int(r[3]), 'modificado': int(r[4]), 'por': r[5]} for r in frows]
    lay, _ = leer_config('formulario')
    return {'sistema': 'cementerio-titulos', 'version': 1, 'fecha': datetime.now(timezone.utc).isoformat(),
            'titulos': {rid: json.loads(d) for rid, d in rows}, 'reporte': rep, 'croquis': croq,
            'formularios': forms, 'formularioAjustes': lay}


def crear_respaldo(motivo):
    u = usuario_actual()
    obj = todo_json()
    q('INSERT INTO respaldos (fecha, motivo, usuario, titulos, data) VALUES (%s,%s,%s,%s,%s)',
      (ahora_ms(), motivo, u['nombre'] if u else '', len(obj['titulos']), json.dumps(obj, ensure_ascii=False, separators=(',', ':'))))
    # conservar los más recientes
    q('DELETE FROM respaldos WHERE id NOT IN (SELECT id FROM respaldos ORDER BY fecha DESC LIMIT %s)', (MAX_RESPALDOS,))


def resumen(data):
    return ('No. ' + str(data.get('noTitulo') or 's/n') + ' · ' + str(data.get('titular') or ''))[:200]


@app.put('/api/titulos/<rid>')
@requiere(*EDITA)
def guardar_titulo(rid):
    if not ID_RE.match(rid):
        return jsonify(error='id_no_valido'), 400
    d = request.get_json(silent=True) or {}
    data, base = d.get('data'), d.get('base')
    if not isinstance(data, dict):
        return jsonify(error='datos_no_validos'), 400
    texto = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    if len(texto) > 250000:
        return jsonify(error='muy_grande', mensaje='El registro es demasiado grande.'), 400
    u = usuario_actual()
    cur = db().cursor()
    cur.execute('SELECT version, borrado, data FROM titulos WHERE id=%s FOR UPDATE', (rid,))
    row = cur.fetchone()
    if row and not int(row[1]):
        if base is None or int(base) != int(row[0]):
            db().rollback()
            return jsonify(error='conflicto', data=json.loads(row[2]), version=int(row[0]),
                           mensaje='Otro usuario modificó este título mientras usted lo editaba.'), 409
    respaldo_diario()
    data['modificadoPor'] = u['nombre']
    texto = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    cur.execute("SELECT nextval('cambios_seq')")
    ver = int(cur.fetchone()[0])
    if row:
        cur.execute('UPDATE titulos SET data=%s, version=%s, borrado=0, modificado=%s, modificado_por=%s WHERE id=%s',
                    (texto, ver, ahora_ms(), u['nombre'], rid))
    else:
        cur.execute('INSERT INTO titulos (id, data, version, modificado, modificado_por) VALUES (%s,%s,%s,%s,%s)',
                    (rid, texto, ver, ahora_ms(), u['nombre']))
    bitacora('editó título' if row and not int(row[1]) else 'creó título', rid, resumen(data))
    db().commit()
    return jsonify(ok=True, version=ver, data=data)


@app.delete('/api/titulos/<rid>')
@requiere(*EDITA)
def borrar_titulo(rid):
    row = q('SELECT data FROM titulos WHERE id=%s AND borrado=0', (rid,), uno=True)
    if not row:
        return jsonify(ok=True)
    respaldo_diario()
    ver = int(q("SELECT nextval('cambios_seq')", uno=True)[0])
    q('UPDATE titulos SET borrado=1, version=%s, modificado=%s, modificado_por=%s WHERE id=%s',
      (ver, ahora_ms(), usuario_actual()['nombre'], rid))
    bitacora('eliminó título', rid, resumen(json.loads(row[0])))
    db().commit()
    return jsonify(ok=True, version=ver)


@app.put('/api/config/reporte')
@requiere(*EDITA)
def guardar_reporte():
    d = request.get_json(silent=True) or {}
    data = d.get('data')
    if not isinstance(data, dict):
        return jsonify(error='datos_no_validos'), 400
    actual, _ = leer_reporte()
    u = usuario_actual()
    if u['rol'] != 'admin':
        for k in ('tarifas', 'prorroga'):
            if json.dumps(actual.get(k) or {}, sort_keys=True) != json.dumps(data.get(k) or {}, sort_keys=True):
                return jsonify(error='sin_permiso', mensaje='Solo un administrador puede cambiar tarifas y ajustes de prórroga.'), 403
    texto = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    if len(texto) > 2000000:
        return jsonify(error='muy_grande'), 400
    ver = int(q("SELECT nextval('cambios_seq')", uno=True)[0])
    q("INSERT INTO config (clave, data, version) VALUES ('reporte',%s,%s) ON CONFLICT (clave) DO UPDATE SET data=EXCLUDED.data, version=EXCLUDED.version",
      (texto, ver))
    if json.dumps(actual.get('tarifas') or {}, sort_keys=True) != json.dumps(data.get('tarifas') or {}, sort_keys=True) or \
       json.dumps(actual.get('prorroga') or {}, sort_keys=True) != json.dumps(data.get('prorroga') or {}, sort_keys=True):
        bitacora('cambió tarifas o ajustes de prórroga')
    db().commit()
    return jsonify(ok=True, version=ver)


# ---------------------------------------------------------------- croquis
def _num(v, lo=-5000, hi=10000):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < lo or v > hi:
        raise ValueError('valor no válido')
    return round(float(v), 1)


def _txt(v, n):
    return str(v or '')[:n]


def validar_boveda(b):
    if not isinstance(b, dict) or not ID_RE.match(str(b.get('id', ''))):
        raise ValueError('bóveda no válida')
    tit = str(b.get('titulo') or '')
    if tit and not ID_RE.match(tit):
        raise ValueError('título no válido')
    return {'id': str(b['id']), 'x': _num(b.get('x')), 'y': _num(b.get('y')), 'r': _num(b.get('r') or 0, -360, 360),
            'tipo': _txt(b.get('tipo'), 30), 'etiqueta': _txt(b.get('etiqueta'), 40), 'titulo': tit,
            'nota': _txt(b.get('nota'), 300), 'creado': _txt(b.get('creado'), 40), 'mod': _txt(b.get('mod'), 40)}


@app.post('/api/croquis')
@requiere(*EDITA)
def croquis_ops():
    d = request.get_json(silent=True) or {}
    ops = d.get('ops')
    if not isinstance(ops, list) or not ops or len(ops) > 200:
        return jsonify(error='datos_no_validos'), 400
    u = usuario_actual()
    if any(isinstance(o, dict) and o.get('op') in ('meta', 'fondo') for o in ops) and u['rol'] != 'admin':
        return jsonify(error='sin_permiso', mensaje='Solo un administrador puede cambiar los límites, sectores o la imagen del croquis.'), 403
    q("INSERT INTO config (clave, data, version) VALUES ('croquis','{}',0) ON CONFLICT (clave) DO NOTHING")
    row = q("SELECT data FROM config WHERE clave='croquis' FOR UPDATE", uno=True)
    st = json.loads(row[0])
    bov = st.get('bovedas') if isinstance(st.get('bovedas'), dict) else {}
    st['bovedas'] = bov
    cambios_bit = []
    try:
        for o in ops:
            if not isinstance(o, dict):
                raise ValueError('operación no válida')
            op = o.get('op')
            if op == 'put':
                b = validar_boveda(o.get('b'))
                antes = bov.get(b['id'])
                if antes is None:
                    cambios_bit.append(('agregó bóveda al croquis', b['titulo'], b['etiqueta']))
                elif antes.get('titulo') != b['titulo']:
                    cambios_bit.append(('cambió el título de una bóveda', b['titulo'] or antes.get('titulo', ''), b['etiqueta']))
                bov[b['id']] = b
                if len(bov) > 20000:
                    raise ValueError('demasiadas bóvedas')
            elif op == 'del':
                bid = str(o.get('id', ''))
                antes = bov.pop(bid, None)
                if antes:
                    cambios_bit.append(('quitó bóveda del croquis', antes.get('titulo', ''), antes.get('etiqueta', '')))
            elif op == 'meta':
                if 'limite' in o:
                    lim = o['limite']
                    if not isinstance(lim, list) or not 3 <= len(lim) <= 500:
                        raise ValueError('límite no válido')
                    st['limite'] = [[_num(p[0]), _num(p[1])] for p in lim]
                if 'cortes' in o:
                    cs = o['cortes']
                    if not isinstance(cs, list) or len(cs) > 20:
                        raise ValueError('divisiones no válidas')
                    st['cortes'] = [[_num(c[0]), _num(c[1]), _num(c[2]), _num(c[3])] for c in cs]
                if 'nombres' in o:
                    ns = o['nombres']
                    if not isinstance(ns, list) or len(ns) > 21:
                        raise ValueError('nombres no válidos')
                    st['nombres'] = [_txt(n, 20) for n in ns]
                if 'tam' in o:
                    st['tam'] = _num(o['tam'], 4, 60)
                cambios_bit.append(('modificó límites o sectores del croquis', '', ''))
            elif op == 'fondo':
                img = o.get('img')
                if img is not None and (not isinstance(img, str) or not img.startswith('data:image/') or len(img) > 4000000):
                    raise ValueError('imagen no válida')
                guardar_config('croquis_fondo', {'img': img})
                cambios_bit.append(('cambió la imagen de fondo del croquis', '', ''))
            else:
                raise ValueError('operación no válida')
    except (ValueError, TypeError, IndexError, KeyError) as e:
        db().rollback()
        return jsonify(error='datos_no_validos', mensaje='No se pudo guardar el croquis: ' + str(e)), 400
    respaldo_diario()
    ver = guardar_config('croquis', st)
    for acc, tid, det in cambios_bit[:20]:
        bitacora(acc, tid, det)
    db().commit()
    return jsonify(ok=True, version=ver)


# ---------------------------------------------------------------- formularios (módulo independiente)
def fila_form(r):
    return {'id': r[0], 'numero': r[1], 'datos': json.loads(r[2]), 'version': int(r[3]), 'creado': int(r[4]), 'modificado': int(r[5]), 'por': r[6]}


@app.get('/api/formularios')
@requiere()
def formularios():
    rows = q('SELECT id, numero, data, version, creado, modificado, modificado_por FROM formularios WHERE borrado=0 ORDER BY modificado DESC', todos=True)
    lay, _ = leer_config('formulario')
    return jsonify(formularios=[fila_form(r) for r in rows], layout=lay)


@app.put('/api/formularios/<fid>')
@requiere(*EDITA)
def guardar_formulario(fid):
    if not ID_RE.match(fid):
        return jsonify(error='id_no_valido'), 400
    d = request.get_json(silent=True) or {}
    datos = d.get('datos')
    if not isinstance(datos, dict) or len(datos) > 100:
        return jsonify(error='datos_no_validos'), 400
    datos = {str(k)[:40]: str(v)[:300] for k, v in datos.items()}
    numero = str(d.get('numero', '')).strip()[:30]
    if not numero:
        return jsonify(error='numero', mensaje='Escriba el N° impreso en la hoja del formulario.'), 400
    u = usuario_actual()
    t = ahora_ms()
    ver = int(q("SELECT nextval('cambios_seq')", uno=True)[0])
    existe = q('SELECT 1 FROM formularios WHERE id=%s AND borrado=0', (fid,), uno=True)
    q('INSERT INTO formularios (id, numero, data, version, borrado, creado, modificado, modificado_por) VALUES (%s,%s,%s,%s,0,%s,%s,%s) '
      'ON CONFLICT (id) DO UPDATE SET numero=EXCLUDED.numero, data=EXCLUDED.data, version=EXCLUDED.version, borrado=0, '
      'modificado=EXCLUDED.modificado, modificado_por=EXCLUDED.modificado_por',
      (fid, numero, json.dumps(datos, ensure_ascii=False), ver, t, t, u['nombre']))
    bitacora(('editó' if existe else 'emitió') + ' formulario de título de puesto', '', 'N° ' + numero + ' · ' + datos.get('favor1', '')[:80])
    db().commit()
    return jsonify(ok=True, version=ver, modificado=t, por=u['nombre'])


@app.delete('/api/formularios/<fid>')
@requiere(*EDITA)
def borrar_formulario(fid):
    row = q('SELECT numero FROM formularios WHERE id=%s AND borrado=0', (fid,), uno=True)
    if row:
        ver = int(q("SELECT nextval('cambios_seq')", uno=True)[0])
        q('UPDATE formularios SET borrado=1, version=%s, modificado=%s, modificado_por=%s WHERE id=%s', (ver, ahora_ms(), usuario_actual()['nombre'], fid))
        bitacora('eliminó formulario de título de puesto', '', 'N° ' + row[0])
        db().commit()
    return jsonify(ok=True)


@app.put('/api/config/formulario')
@requiere('admin')
def guardar_ajustes_formulario():
    d = request.get_json(silent=True) or {}
    data = d.get('data')
    if not isinstance(data, dict):
        return jsonify(error='datos_no_validos'), 400
    limpio = {'dx': float(data.get('dx') or 0), 'dy': float(data.get('dy') or 0), 'fuente': float(data.get('fuente') or 10), 'campos': {},
              'plantilla': str(data.get('plantilla') or '')[:10]}
    for k, v in (data.get('campos') or {}).items():
        if isinstance(v, dict) and len(limpio['campos']) < 200:
            limpio['campos'][str(k)[:40]] = {'dx': float(v.get('dx') or 0), 'dy': float(v.get('dy') or 0)}
    guardar_config('formulario', limpio)
    bitacora('ajustó posiciones de impresión del formulario')
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- respaldos e importación
def descarga(obj, nombre):
    datos = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return Response(datos, mimetype='application/json',
                    headers={'Content-Disposition': 'attachment; filename="' + nombre + '"', 'Cache-Control': 'no-store'})


@app.get('/api/respaldo')
@requiere(*EDITA)
def bajar_respaldo():
    bitacora('descargó un respaldo')
    db().commit()
    return descarga(todo_json(), 'respaldo_cementerio_' + datetime.now(TZ).strftime('%Y-%m-%d_%H-%M') + '.json')


def reemplazar_todo(obj, motivo):
    titulos = obj.get('titulos')
    if not isinstance(titulos, dict) or not titulos:
        raise ValueError('El archivo no contiene títulos.')
    for rid, data in titulos.items():
        if not ID_RE.match(str(rid)) or not isinstance(data, dict):
            raise ValueError('El archivo tiene un registro no válido: ' + str(rid)[:40])
    if int(q('SELECT COUNT(*) FROM titulos WHERE borrado=0', uno=True)[0]) > 0:
        crear_respaldo('antes de ' + motivo)
    u = usuario_actual()
    cur = db().cursor()
    cur.execute("SELECT nextval('cambios_seq')")
    ver = int(cur.fetchone()[0])
    cur.execute('UPDATE titulos SET borrado=1, version=%s WHERE borrado=0', (ver,))
    t = ahora_ms()
    for rid, data in titulos.items():
        texto = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        cur.execute("SELECT nextval('cambios_seq')")
        v = int(cur.fetchone()[0])
        cur.execute('INSERT INTO titulos (id, data, version, borrado, modificado, modificado_por) VALUES (%s,%s,%s,0,%s,%s) '
                    'ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, version=EXCLUDED.version, borrado=0, '
                    'modificado=EXCLUDED.modificado, modificado_por=EXCLUDED.modificado_por',
                    (str(rid), texto, v, t, u['nombre']))
    if isinstance(obj.get('reporte'), dict):
        cur.execute("SELECT nextval('cambios_seq')")
        v = int(cur.fetchone()[0])
        cur.execute("INSERT INTO config (clave, data, version) VALUES ('reporte',%s,%s) ON CONFLICT (clave) DO UPDATE SET data=EXCLUDED.data, version=EXCLUDED.version",
                    (json.dumps(obj['reporte'], ensure_ascii=False, separators=(',', ':')), v))
    if isinstance(obj.get('croquis'), dict):
        croq = dict(obj['croquis'])
        img = croq.pop('fondo', None)
        guardar_config('croquis', croq)
        guardar_config('croquis_fondo', {'img': img if isinstance(img, str) and img.startswith('data:image/') else None})
    if isinstance(obj.get('formularios'), list):
        cur.execute("SELECT nextval('cambios_seq')")
        v = int(cur.fetchone()[0])
        cur.execute('UPDATE formularios SET borrado=1, version=%s WHERE borrado=0', (v,))
        for f in obj['formularios'][:50000]:
            if not isinstance(f, dict) or not ID_RE.match(str(f.get('id', ''))) or not isinstance(f.get('datos'), dict):
                continue
            cur.execute('INSERT INTO formularios (id, numero, data, version, borrado, creado, modificado, modificado_por) VALUES (%s,%s,%s,%s,0,%s,%s,%s) '
                        'ON CONFLICT (id) DO UPDATE SET numero=EXCLUDED.numero, data=EXCLUDED.data, version=EXCLUDED.version, borrado=0, '
                        'modificado=EXCLUDED.modificado, modificado_por=EXCLUDED.modificado_por',
                        (str(f['id']), str(f.get('numero', ''))[:30], json.dumps(f['datos'], ensure_ascii=False), v,
                         int(f.get('creado') or 0), int(f.get('modificado') or 0), str(f.get('por', ''))[:80]))
    if isinstance(obj.get('formularioAjustes'), dict):
        guardar_config('formulario', obj['formularioAjustes'])
    return len(titulos)


@app.post('/api/importar')
@requiere('admin')
def importar():
    obj = request.get_json(silent=True)
    if not isinstance(obj, dict):
        return jsonify(error='archivo_no_valido', mensaje='El archivo no es un respaldo de este sistema.'), 400
    try:
        n = reemplazar_todo(obj, 'importar')
    except ValueError as e:
        db().rollback()
        return jsonify(error='archivo_no_valido', mensaje=str(e)), 400
    bitacora('importó datos', '', str(n) + ' títulos')
    db().commit()
    return jsonify(ok=True, titulos=n)


@app.get('/api/respaldos')
@requiere('admin')
def lista_respaldos():
    rows = q('SELECT id, fecha, motivo, usuario, titulos, LENGTH(data) FROM respaldos ORDER BY fecha DESC', todos=True)
    return jsonify(respaldos=[{'id': int(r[0]), 'fecha': int(r[1]), 'motivo': r[2], 'usuario': r[3], 'titulos': int(r[4]), 'tam': int(r[5])} for r in rows])


@app.post('/api/respaldos')
@requiere('admin')
def nuevo_respaldo():
    crear_respaldo('manual')
    bitacora('creó un respaldo manual')
    db().commit()
    return jsonify(ok=True)


@app.get('/api/respaldos/<int:bid>')
@requiere('admin')
def bajar_respaldo_guardado(bid):
    row = q('SELECT data, fecha FROM respaldos WHERE id=%s', (bid,), uno=True)
    if not row:
        return jsonify(error='no_existe'), 404
    f = datetime.fromtimestamp(int(row[1]) / 1000, TZ).strftime('%Y-%m-%d_%H-%M')
    return descarga(json.loads(row[0]), 'respaldo_cementerio_' + f + '.json')


@app.post('/api/respaldos/<int:bid>/restaurar')
@requiere('admin')
def restaurar_respaldo(bid):
    row = q('SELECT data, fecha FROM respaldos WHERE id=%s', (bid,), uno=True)
    if not row:
        return jsonify(error='no_existe'), 404
    try:
        n = reemplazar_todo(json.loads(row[0]), 'restaurar')
    except ValueError as e:
        db().rollback()
        return jsonify(error='archivo_no_valido', mensaje=str(e)), 400
    bitacora('restauró un respaldo', '', datetime.fromtimestamp(int(row[1]) / 1000, TZ).strftime('%d/%m/%Y %H:%M'))
    db().commit()
    return jsonify(ok=True, titulos=n)


# ---------------------------------------------------------------- usuarios
def fila_usuario(r):
    return {'id': int(r[0]), 'usuario': r[1], 'nombre': r[2], 'rol': r[3], 'activo': int(r[4]) == 1, 'cambiar': int(r[5]) == 1}


def admins_activos(excepto=None):
    r = q("SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1 AND id<>%s", (excepto or 0,), uno=True)
    return int(r[0])


@app.get('/api/usuarios')
@requiere('admin')
def usuarios():
    rows = q('SELECT id, usuario, nombre, rol, activo, cambiar FROM usuarios ORDER BY activo DESC, nombre', todos=True)
    return jsonify(usuarios=[fila_usuario(r) for r in rows])


@app.post('/api/usuarios')
@requiere('admin')
def crear_usuario():
    d = request.get_json(silent=True) or {}
    usuario = str(d.get('usuario', '')).strip().lower()
    nombre = str(d.get('nombre', '')).strip()[:80]
    clave = str(d.get('clave', ''))
    rol = d.get('rol')
    if not USER_RE.match(usuario):
        return jsonify(error='usuario', mensaje='El usuario debe tener de 3 a 30 letras minúsculas, números, punto o guion, sin espacios.'), 400
    if rol not in ROLES:
        return jsonify(error='rol'), 400
    if len(clave) < 8:
        return jsonify(error='clave_corta', mensaje='La clave debe tener al menos 8 caracteres.'), 400
    if q('SELECT 1 FROM usuarios WHERE usuario=%s', (usuario,), uno=True):
        return jsonify(error='existe', mensaje='Ya existe un usuario con ese nombre.'), 400
    q('INSERT INTO usuarios (usuario, nombre, clave, rol, cambiar, creado) VALUES (%s,%s,%s,%s,1,%s)',
      (usuario, nombre or usuario, generate_password_hash(clave), rol, ahora_ms()))
    bitacora('creó el usuario ' + usuario, '', rol)
    db().commit()
    return usuarios()


@app.put('/api/usuarios/<int:uid>')
@requiere('admin')
def editar_usuario(uid):
    d = request.get_json(silent=True) or {}
    row = q('SELECT id, usuario, nombre, rol, activo, cambiar, sv FROM usuarios WHERE id=%s', (uid,), uno=True)
    if not row:
        return jsonify(error='no_existe'), 404
    nombre = str(d.get('nombre', row[2])).strip()[:80] or row[1]
    rol = d.get('rol', row[3])
    activo = 1 if d.get('activo', int(row[4]) == 1) else 0
    if rol not in ROLES:
        return jsonify(error='rol'), 400
    if (rol != 'admin' or not activo) and row[3] == 'admin' and admins_activos(excepto=uid) == 0:
        return jsonify(error='ultimo_admin', mensaje='Debe quedar al menos un administrador activo.'), 400
    sv = int(row[6])
    clave = d.get('clave')
    if clave:
        if len(str(clave)) < 8:
            return jsonify(error='clave_corta', mensaje='La clave debe tener al menos 8 caracteres.'), 400
        sv += 1
        q('UPDATE usuarios SET clave=%s, cambiar=1 WHERE id=%s', (generate_password_hash(str(clave)), uid))
    if not activo or rol != row[3]:
        sv += 1  # cierra las sesiones abiertas de ese usuario
    q('UPDATE usuarios SET nombre=%s, rol=%s, activo=%s, sv=%s WHERE id=%s', (nombre, rol, activo, sv, uid))
    bitacora('modificó el usuario ' + row[1], '', ('nueva clave; ' if clave else '') + rol + ('' if activo else '; desactivado'))
    db().commit()
    if uid == usuario_actual()['id']:
        session['sv'] = sv
    return usuarios()


@app.get('/api/bitacora')
@requiere(*EDITA)
def ver_bitacora():
    tid = request.args.get('titulo', '')
    if tid:
        rows = q('SELECT fecha, usuario, accion, titulo_id, detalle FROM bitacora WHERE titulo_id=%s ORDER BY id DESC LIMIT 50', (tid,), todos=True)
    else:
        if usuario_actual()['rol'] != 'admin':
            return jsonify(error='sin_permiso'), 403
        rows = q('SELECT fecha, usuario, accion, titulo_id, detalle FROM bitacora ORDER BY id DESC LIMIT 300', todos=True)
    return jsonify(bitacora=[{'fecha': int(r[0]), 'usuario': r[1], 'accion': r[2], 'titulo': r[3], 'detalle': r[4]} for r in rows])


@app.errorhandler(404)
def no_existe(e):
    if request.path.startswith('/api/'):
        return jsonify(error='no_existe', mensaje='Esa función no existe en el servidor.'), 404
    return 'No encontrado', 404


@app.errorhandler(500)
def error_interno(e):
    return jsonify(error='error_servidor', mensaje='Ocurrió un error en el servidor. Si se repite, revise los registros (logs) en Railway.'), 500


@app.errorhandler(413)
def muy_grande(e):
    return jsonify(error='muy_grande', mensaje='El archivo es demasiado grande.'), 413


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')), debug=False)
