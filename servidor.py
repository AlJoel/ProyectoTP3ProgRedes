import json
import itertools
import queue
import socket
import sqlite3
import threading
import time

from werkzeug.security import generate_password_hash, check_password_hash

HOST = "127.0.0.1"
PUERTO = 9000
DB_FILE = "tareas.db"
CANTIDAD_WORKERS = 3

# Cola interna usada para repartir las solicitudes entre workers.
# En el diagrama aparece RabbitMQ, pero en el código se usa Queue para no depender
# de servicios externos.
cola_solicitudes = queue.Queue()

# Contador simple para identificar cada solicitud en los logs.
contador_solicitudes = itertools.count(1)

# Sesiones como en el TP2: se guarda la IP del cliente y el usuario logueado.
# Formato: { "ip_cliente": "usuario" }
sesiones_iniciadas = {}

# Locks para evitar problemas entre hilos.
log_lock = threading.Lock()
sesiones_lock = threading.Lock()


def log(origen, mensaje):
    hora = time.strftime("%H:%M:%S")
    with log_lock:
        print(f"[{hora}] [{origen}] {mensaje}", flush=True)


def conectar_db():
    return sqlite3.connect(DB_FILE)


def inicializar_db():
    conexion = conectar_db()
    cursor = conexion.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            contrasena TEXT NOT NULL
        )
    ''')

    conexion.commit()
    conexion.close()


def recibir_json_linea(conexion):
    buffer = b""

    while b"\n" not in buffer:
        parte = conexion.recv(4096)
        if not parte:
            break
        buffer += parte

    if not buffer:
        return None

    linea = buffer.decode("utf-8").strip()
    return json.loads(linea)


def enviar_json(conexion, datos):
    mensaje = json.dumps(datos, ensure_ascii=False) + "\n"
    conexion.sendall(mensaje.encode("utf-8"))


def respuesta(ok, mensaje, id_solicitud, worker_id=None, **extra):
    datos = {
        "ok": ok,
        "mensaje": mensaje,
        "id_solicitud": id_solicitud,
    }

    if worker_id is not None:
        datos["worker"] = worker_id

    datos.update(extra)
    return datos


def registrar_usuario(datos, id_solicitud, worker_id):
    usuario = datos.get("usuario")
    contrasena = datos.get("contrasena")

    if not usuario or not contrasena:
        return respuesta(False, "Faltan datos", id_solicitud, worker_id)

    # Se mantiene contraseña hasheada con Werkzeug.
    contrasena_hasheada = generate_password_hash(contrasena)

    try:
        conexion = conectar_db()
        cursor = conexion.cursor()
        cursor.execute(
            "INSERT INTO usuarios (usuario, contrasena) VALUES (?, ?)",
            (usuario, contrasena_hasheada)
        )
        conexion.commit()
        conexion.close()

        return respuesta(True, "Usuario registrado correctamente", id_solicitud, worker_id)

    except sqlite3.IntegrityError:
        return respuesta(False, "El usuario ya existe", id_solicitud, worker_id)


def iniciar_sesion(datos, ip_cliente, id_solicitud, worker_id):
    usuario = datos.get("usuario")
    contrasena = datos.get("contrasena")

    if not usuario or not contrasena:
        return respuesta(False, "Faltan datos", id_solicitud, worker_id)

    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("SELECT contrasena FROM usuarios WHERE usuario = ?", (usuario,))
    resultado = cursor.fetchone()
    conexion.close()

    if resultado and check_password_hash(resultado[0], contrasena):
        with sesiones_lock:
            sesiones_iniciadas[ip_cliente] = usuario

        return respuesta(
            True,
            "Inicio de sesión correcto",
            id_solicitud,
            worker_id,
            usuario=usuario,
            ip=ip_cliente,
        )

    return respuesta(False, "Usuario o contraseña incorrectos", id_solicitud, worker_id)


def cerrar_sesion(ip_cliente, id_solicitud, worker_id):
    with sesiones_lock:
        if ip_cliente in sesiones_iniciadas:
            usuario = sesiones_iniciadas[ip_cliente]
            del sesiones_iniciadas[ip_cliente]
            return respuesta(
                True,
                f"Sesión cerrada para el usuario {usuario}",
                id_solicitud,
                worker_id,
            )

    return respuesta(False, "No había una sesión iniciada para esta IP", id_solicitud, worker_id)


def ver_tareas(ip_cliente, id_solicitud, worker_id):
    with sesiones_lock:
        usuario = sesiones_iniciadas.get(ip_cliente)

    if not usuario:
        return respuesta(
            False,
            "Acceso denegado. Primero debe iniciar sesión.",
            id_solicitud,
            worker_id,
        )

    # Listado simple de tareas disponibles.
    tareas = [
        "1 - Estudiar sockets",
        "2 - Terminar TP de redes",
        "3 - Probar API REST",
    ]

    return respuesta(
        True,
        "Tareas obtenidas correctamente",
        id_solicitud,
        worker_id,
        usuario=usuario,
        ip=ip_cliente,
        tareas=tareas,
    )


def procesar_solicitud(datos, ip_cliente, id_solicitud, worker_id):
    accion = datos.get("accion")

    log(f"WORKER {worker_id}", f"Procesando acción '{accion}' de la solicitud #{id_solicitud}")

    if accion == "registro":
        resultado = registrar_usuario(datos, id_solicitud, worker_id)
    elif accion == "login":
        resultado = iniciar_sesion(datos, ip_cliente, id_solicitud, worker_id)
    elif accion == "tareas":
        resultado = ver_tareas(ip_cliente, id_solicitud, worker_id)
    elif accion == "logout":
        resultado = cerrar_sesion(ip_cliente, id_solicitud, worker_id)
    else:
        resultado = respuesta(False, "Acción no reconocida", id_solicitud, worker_id)

    resultado["recorrido"] = [
        "1. El cliente envió la solicitud por socket TCP",
        f"2. El servidor recibió la solicitud #{id_solicitud}",
        "3. El servidor colocó la solicitud en la cola interna",
        f"4. El Worker {worker_id} tomó la solicitud y la procesó",
        "5. El servidor envió la respuesta al cliente",
    ]

    return resultado


def worker_loop(worker_id):
    log(f"WORKER {worker_id}", "Iniciado y esperando solicitudes")

    while True:
        id_solicitud, datos, ip_cliente, cola_respuesta = cola_solicitudes.get()

        try:
            log(f"WORKER {worker_id}", f"Tomó la solicitud #{id_solicitud} desde la cola")
            resultado = procesar_solicitud(datos, ip_cliente, id_solicitud, worker_id)
            log(f"WORKER {worker_id}", f"Finalizó la solicitud #{id_solicitud}")
        except Exception as error:
            resultado = respuesta(
                False,
                f"Error interno en el worker: {error}",
                id_solicitud,
                worker_id,
            )
            log(f"WORKER {worker_id}", f"Error en la solicitud #{id_solicitud}: {error}")

        cola_respuesta.put(resultado)
        cola_solicitudes.task_done()


def manejar_cliente(conexion, direccion):
    id_solicitud = next(contador_solicitudes)
    ip_cliente, puerto_cliente = direccion

    log("CLIENTE", f"Conexión recibida desde {ip_cliente}:{puerto_cliente} | Solicitud #{id_solicitud}")

    try:
        datos = recibir_json_linea(conexion)

        if datos is None:
            enviar_json(conexion, respuesta(False, "No se recibieron datos", id_solicitud))
            return

        log("SOCKET", f"Solicitud #{id_solicitud} recibida: {datos}")

        cola_respuesta = queue.Queue(maxsize=1)
        cola_solicitudes.put((id_solicitud, datos, ip_cliente, cola_respuesta))
        log("COLA", f"Solicitud #{id_solicitud} enviada a la cola interna")

        resultado = cola_respuesta.get(timeout=10)
        log("RESPUESTA", f"Enviando respuesta de la solicitud #{id_solicitud} al cliente")
        enviar_json(conexion, resultado)

    except json.JSONDecodeError:
        log("ERROR", f"JSON inválido en la solicitud #{id_solicitud}")
        enviar_json(conexion, respuesta(False, "El mensaje recibido no es un JSON válido", id_solicitud))

    except queue.Empty:
        log("ERROR", f"Timeout en la solicitud #{id_solicitud}")
        enviar_json(conexion, respuesta(False, "Tiempo de espera agotado procesando la solicitud", id_solicitud))

    except Exception as error:
        log("ERROR", f"Error en la solicitud #{id_solicitud}: {error}")
        enviar_json(conexion, respuesta(False, f"Error al atender al cliente: {error}", id_solicitud))

    finally:
        conexion.close()
        log("CLIENTE", f"Conexión cerrada para la solicitud #{id_solicitud}")


def iniciar_workers():
    for worker_id in range(1, CANTIDAD_WORKERS + 1):
        hilo = threading.Thread(target=worker_loop, args=(worker_id,), daemon=True)
        hilo.start()


def iniciar_servidor():
    # Al iniciar el servidor no se conserva ninguna sesión anterior.
    sesiones_iniciadas.clear()
    inicializar_db()
    iniciar_workers()

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PUERTO))
    servidor.listen()

    log("SERVIDOR", f"Escuchando en {HOST}:{PUERTO}")
    log("SERVIDOR", f"Workers iniciados: {CANTIDAD_WORKERS}")

    while True:
        conexion, direccion = servidor.accept()
        hilo_cliente = threading.Thread(
            target=manejar_cliente,
            args=(conexion, direccion),
            daemon=True,
        )
        hilo_cliente.start()


if __name__ == "__main__":
    iniciar_servidor()
