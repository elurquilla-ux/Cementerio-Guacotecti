'use strict';
/*
 * Cementerio General · Control de títulos
 * Proceso principal: guarda los datos en un archivo y crea respaldos automáticos.
 *
 *   Documentos\Cementerio General\datos.json        ← datos actuales (se guarda en cada cambio)
 *   Documentos\Cementerio General\Respaldos\        ← una copia con fecha cada vez que se cierra
 *   Documentos\Cementerio General\Reportes\         ← carpeta sugerida para Excel y PDF
 */
const { app, BrowserWindow, ipcMain, dialog, shell, Menu, session } = require('electron');
const fs = require('fs');
const path = require('path');

const APP_NAME = 'Cementerio General';
app.setName(APP_NAME);
if (process.platform === 'win32') app.setAppUserModelId('sv.cementerio.titulos');

// Una sola ventana abierta a la vez: dos copias del programa podrían pisarse los cambios.
if (!app.requestSingleInstanceLock()) { app.quit(); process.exit(0); }

const BASE_DIR = process.env.CEMENTERIO_DIR || path.join(app.getPath('documents'), APP_NAME);
const DATA = path.join(BASE_DIR, 'datos.json');
const BK_DIR = path.join(BASE_DIR, 'Respaldos');
const REP_DIR = path.join(BASE_DIR, 'Reportes');
const CFG_FILE = path.join(BASE_DIR, 'configuracion.json');
const KEEP_DEFAULT = 60;

let win = null;
let changedSinceBackup = false;
let backupDone = false;
let cfg = { extra: '', keep: KEEP_DEFAULT, lastBackup: '', lastExtraError: '' };

/* ---------- utilidades de archivos ---------- */
function ensureDirs() { [BASE_DIR, BK_DIR, REP_DIR].forEach(d => fs.mkdirSync(d, { recursive: true })); }
function writeAtomic(file, text) {
  const tmp = file + '.tmp';
  const fd = fs.openSync(tmp, 'w');
  try { fs.writeSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, file);
}
function loadCfg() {
  try { cfg = Object.assign(cfg, JSON.parse(fs.readFileSync(CFG_FILE, 'utf8'))); } catch (e) { /* primera vez */ }
  if (!(cfg.keep >= 5)) cfg.keep = KEEP_DEFAULT;
}
function saveCfg() { try { writeAtomic(CFG_FILE, JSON.stringify(cfg, null, 2)); } catch (e) { /* no crítico */ } }
const pad = n => String(n).padStart(2, '0');
function stamp(d = new Date()) {
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + '_' + pad(d.getHours()) + '-' + pad(d.getMinutes()) + '-' + pad(d.getSeconds());
}
function listBackups() {
  let names = [];
  try { names = fs.readdirSync(BK_DIR).filter(n => /^respaldo_.*\.json$/i.test(n)); } catch (e) { return []; }
  return names.map(n => {
    const st = fs.statSync(path.join(BK_DIR, n));
    return { name: n, size: st.size, mtime: st.mtime.toISOString() };
  }).sort((a, b) => b.mtime.localeCompare(a.mtime) || b.name.localeCompare(a.name));
}
function validData(obj) { return obj && typeof obj === 'object' && obj.titulos && typeof obj.titulos === 'object'; }

/* ---------- respaldo ---------- */
function backup(reason) {
  if (!fs.existsSync(DATA)) return { ok: false, error: 'Todavía no hay datos guardados.' };
  ensureDirs();
  const suffix = reason === 'antes de restaurar' ? '_antes-de-restaurar' : reason === 'manual' ? '_manual' : '';
  const name = 'respaldo_' + stamp() + suffix + '.json';
  const dest = path.join(BK_DIR, name);
  fs.copyFileSync(DATA, dest);
  cfg.lastBackup = new Date().toISOString();
  changedSinceBackup = false;

  // copia adicional (memoria USB, OneDrive, carpeta de red)
  let extraOk = null;
  if (cfg.extra) {
    try {
      if (!fs.existsSync(cfg.extra)) throw new Error('No se encontró la carpeta');
      const extraDir = path.join(cfg.extra, 'Respaldos Cementerio General');
      fs.mkdirSync(extraDir, { recursive: true });
      fs.copyFileSync(DATA, path.join(extraDir, name));
      pruneDir(extraDir, cfg.keep);
      cfg.lastExtraError = '';
      extraOk = true;
    } catch (e) {
      cfg.lastExtraError = new Date().toISOString() + '|' + e.message;
      extraOk = false;
    }
  }
  pruneDir(BK_DIR, cfg.keep);
  saveCfg();
  return { ok: true, name, extraOk };
}
function pruneDir(dir, keep) {
  try {
    // los respaldos "antes de restaurar" y "manual" también cuentan; se eliminan los más antiguos
    const files = fs.readdirSync(dir).filter(n => /^respaldo_.*\.json$/i.test(n))
      .map(n => ({ n, t: fs.statSync(path.join(dir, n)).mtimeMs }))
      .sort((a, b) => b.t - a.t);
    files.slice(keep).forEach(f => { try { fs.unlinkSync(path.join(dir, f.n)); } catch (e) { } });
  } catch (e) { }
}
function backupOnExit() {
  if (backupDone) return;
  backupDone = true;
  // Se respalda si hubo cambios en esta sesión o si hoy aún no hay respaldo.
  const today = new Date().toDateString();
  const last = cfg.lastBackup ? new Date(cfg.lastBackup).toDateString() : '';
  if (changedSinceBackup || last !== today) {
    try { backup('cierre'); } catch (e) { /* se reintentará la próxima vez */ }
  }
}

/* ---------- comunicación con la ventana ---------- */
ipcMain.on('data:load', e => {
  try {
    if (!fs.existsSync(DATA)) { e.returnValue = null; return; }
    const obj = JSON.parse(fs.readFileSync(DATA, 'utf8'));
    if (!validData(obj)) throw new Error('Formato no reconocido');
    e.returnValue = obj;
  } catch (err) {
    // datos.json dañado: se recupera del respaldo más reciente que sea válido
    try { fs.copyFileSync(DATA, path.join(BASE_DIR, 'datos_danado_' + stamp() + '.json')); } catch (x) { }
    for (const b of listBackups()) {
      try {
        const obj = JSON.parse(fs.readFileSync(path.join(BK_DIR, b.name), 'utf8'));
        if (validData(obj)) {
          writeAtomic(DATA, JSON.stringify(obj));
          obj.recuperado = b.name;
          e.returnValue = obj;
          return;
        }
      } catch (x) { }
    }
    e.returnValue = { error: String(err && err.message || err) };
  }
});
ipcMain.on('data:save', (e, text) => {
  try {
    if (typeof text !== 'string' || text.length < 2) throw new Error('Datos vacíos');
    ensureDirs();
    writeAtomic(DATA, text);
    changedSinceBackup = true;
    e.returnValue = { ok: true };
  } catch (err) {
    e.returnValue = { ok: false, error: String(err && err.message || err) };
  }
});
ipcMain.handle('bk:info', () => ({
  dataFile: DATA, backupDir: BK_DIR, extra: cfg.extra, keep: cfg.keep,
  lastBackup: cfg.lastBackup, lastExtraError: cfg.lastExtraError,
  backups: listBackups().slice(0, 15), total: listBackups().length
}));
ipcMain.handle('bk:now', (e, reason) => { try { return backup(reason || 'manual'); } catch (err) { return { ok: false, error: err.message }; } });
ipcMain.handle('bk:openFolder', () => shell.openPath(BK_DIR));
ipcMain.handle('bk:chooseExtra', async () => {
  const r = await dialog.showOpenDialog(win, {
    title: 'Elegir carpeta para la copia adicional (memoria USB, OneDrive...)',
    properties: ['openDirectory', 'createDirectory']
  });
  if (r.canceled || !r.filePaths[0]) return { ok: false };
  cfg.extra = r.filePaths[0]; cfg.lastExtraError = ''; saveCfg();
  return { ok: true, extra: cfg.extra };
});
ipcMain.handle('bk:clearExtra', () => { cfg.extra = ''; cfg.lastExtraError = ''; saveCfg(); return { ok: true }; });
ipcMain.handle('bk:read', (e, name) => {
  if (typeof name !== 'string' || !/^respaldo_[\w\-]+\.json$/i.test(name)) return { ok: false, error: 'Nombre no válido' };
  try {
    const obj = JSON.parse(fs.readFileSync(path.join(BK_DIR, name), 'utf8'));
    if (!validData(obj)) throw new Error('El respaldo no tiene el formato esperado');
    return { ok: true, data: obj };
  } catch (err) { return { ok: false, error: err.message }; }
});
ipcMain.handle('pdf:save', async (e, opts) => {
  opts = opts || {};
  ensureDirs();
  const r = await dialog.showSaveDialog(win, {
    title: 'Guardar reporte en PDF',
    defaultPath: path.join(REP_DIR, String(opts.filename || 'Reporte.pdf').replace(/[\\/:*?"<>|]/g, '_')),
    filters: [{ name: 'PDF', extensions: ['pdf'] }]
  });
  if (r.canceled || !r.filePath) return { ok: false, canceled: true };
  try {
    const buf = await win.webContents.printToPDF({
      pageSize: 'Letter', landscape: !!opts.landscape, printBackground: false, preferCSSPageSize: true,
      margins: { marginType: 'custom', top: 0.45, bottom: 0.45, left: 0.45, right: 0.45 }
    });
    fs.writeFileSync(r.filePath, buf);
    shell.openPath(r.filePath);
    return { ok: true, file: r.filePath };
  } catch (err) { return { ok: false, error: err.message }; }
});

/* ---------- ventana ---------- */
function createWindow() {
  win = new BrowserWindow({
    width: 1320, height: 860, minWidth: 900, minHeight: 600,
    title: APP_NAME, show: false, backgroundColor: '#EEF0EC',
    icon: path.join(__dirname, process.platform === 'win32' ? 'icono.ico' : 'icono.png'),
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false }
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'Ver', submenu: [{ role: 'zoomIn', label: 'Acercar' }, { role: 'zoomOut', label: 'Alejar' }, { role: 'resetZoom', label: 'Tamaño normal' }, { type: 'separator' }, { role: 'togglefullscreen', label: 'Pantalla completa' }] }
  ]));
  win.setMenuBarVisibility(false);
  win.once('ready-to-show', () => { win.maximize(); win.show(); });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith('file:')) e.preventDefault(); });
  win.loadFile(path.join(__dirname, 'index.html'));
  // 'closed' llega después de que la página guardó sus últimos cambios (beforeunload)
  win.on('closed', () => { backupOnExit(); win = null; });
}

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.whenReady().then(() => {
  ensureDirs(); loadCfg();
  session.defaultSession.on('will-download', (e, item) => {
    item.setSaveDialogOptions({ title: 'Guardar archivo', defaultPath: path.join(REP_DIR, item.getFilename()) });
  });
  createWindow();
});
app.on('window-all-closed', () => { backupOnExit(); app.quit(); });
app.on('before-quit', () => { /* el respaldo se hace al cerrar la ventana */ });
// Apagado o cierre de sesión de Windows con el programa abierto
app.on('session-end', () => { try { backupOnExit(); } catch (e) { } });
