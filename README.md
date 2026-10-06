# Cementerio General de Guacotecti · versión web

Sistema de control de títulos, espacios, prórrogas y reportes de servicios.
Funciona en el navegador (computadora o celular); los datos se guardan en una base PostgreSQL en Railway y todos los usuarios ven la misma información.

> **Importante:** este repositorio **no contiene datos personales**. Los títulos se cargan después desde el sistema, con un archivo de respaldo. **Nunca suba a GitHub archivos de respaldo (`respaldo_*.json`) ni `datos_iniciales_cementerio.json`.**

---

## Paso a paso para publicarlo

### 1. Subir los archivos a GitHub

1. Entre a <https://github.com> con su cuenta.
2. Arriba a la derecha: **+** → **New repository**.
3. Nombre: `cementerio-guacotecti`. Marque **Private**. No marque ninguna otra opción. Clic en **Create repository**.
4. En la página del repositorio nuevo, haga clic en el enlace **uploading an existing file**.
5. Abra la carpeta `cementerio-web` que descomprimió y **arrastre todo su contenido** a la página: `app.py`, `requirements.txt`, `Procfile`, `railway.json`, `README.md` e `index.html` (o la carpeta `static`).
   - Arrastre el **contenido** de la carpeta, no la carpeta misma ni el .zip.
   - `index.html` puede ir suelto o dentro de la carpeta `static`; los dos funcionan.
6. Abajo, clic en **Commit changes**.

### 2. Crear el proyecto en Railway

1. Entre a <https://railway.com> con su cuenta.
2. **New Project** → **Deploy from GitHub repo** → elija `cementerio-guacotecti`.
   (Si no aparece, use **Configure GitHub App** y dé acceso a ese repositorio.)
3. Railway empieza a instalarlo. El primer intento puede fallar porque todavía falta la base de datos; es normal.

### 3. Agregar la base de datos

1. Dentro del proyecto: **+ Create** (o **New**) → **Database** → **PostgreSQL**.
2. Espere a que el cuadro de Postgres quede en verde.

### 4. Configurar las variables

Haga clic en el servicio de la aplicación (no en Postgres) → pestaña **Variables** → **New Variable**, y agregue:

| Variable | Valor |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (escríbalo tal cual; Railway lo reemplaza por la conexión real) |
| `ADMIN_USUARIO` | `admin` (o el nombre de usuario que prefiera, en minúsculas) |
| `ADMIN_CLAVE` | una clave temporal de al menos 8 caracteres |

Al guardar, Railway vuelve a desplegar solo.

### 5. Obtener la dirección web

1. En el servicio de la aplicación: **Settings** → **Networking** → **Generate Domain**.
2. Railway le da una dirección como `cementerio-guacotecti-production.up.railway.app`.
3. Ábrala en el navegador. Debe ver la pantalla de inicio de sesión.

### 6. Primer ingreso

1. Entre con `ADMIN_USUARIO` y `ADMIN_CLAVE`.
2. El sistema le pide crear una **clave personal**. Hágalo.
3. Aparece **Cargar los datos** → **Importar archivo…** y elija el respaldo más reciente:
   - del **programa instalado**: botón **Respaldos** → **Respaldar ahora** → **Abrir carpeta** → el archivo más reciente;
   - o `datos_iniciales_cementerio.json` (los 1,095 títulos del Excel original).
4. Revise que aparezcan los títulos.

### 7. Crear los usuarios del personal

Botón con su nombre (arriba) → **Usuarios** → **Agregar usuario**:

- **Administrador**: todo, incluidas tarifas, usuarios, importar y restaurar.
- **Editor**: registra títulos, cobra prórrogas, llena reportes.
- **Solo consulta**: busca, ve fichas e imprime estados de cuenta.

Cada persona entra con la clave temporal que usted le asigne y el sistema le pide cambiarla.

---

## Respaldos

- Cada cambio se guarda al instante en la base de datos.
- Cada día, antes del primer cambio, el sistema guarda una copia completa. Se conservan las últimas 40 (botón **Respaldos**).
- **Recomendado:** una vez por semana, **Respaldos → Descargar respaldo**, y guarde el archivo en una memoria USB o en la nube.

## Actualizar el sistema más adelante

Cuando reciba archivos nuevos, en GitHub abra el repositorio → **Add file → Upload files**, arrastre los archivos nuevos (reemplazan a los anteriores) → **Commit changes**. Railway se actualiza solo en uno o dos minutos. Los datos no se tocan.

## Si algo falla

- **La página dice que no se pudo conectar con la base de datos:** revise que `DATABASE_URL` esté escrita exactamente como en la tabla y que Postgres esté en verde.
- **Dice que falta configurar ADMIN_CLAVE:** agregue esa variable.
- **Olvidó la clave del único administrador:** otro administrador puede asignarle una nueva en **Usuarios**. Si no hay otro, escriba al soporte técnico que le instaló el sistema.
- Para ver los mensajes del servidor: en Railway, servicio de la aplicación → **Deployments** → **View logs**.
