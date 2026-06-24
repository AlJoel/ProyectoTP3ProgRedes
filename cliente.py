import json
import socket
import time

HOST = "127.0.0.1"
PUERTO = 9000


def log(mensaje):
    hora = time.strftime("%H:%M:%S")
    print(f"[{hora}] [CLIENTE] {mensaje}")


def enviar_solicitud(datos):
    log("Abriendo socket TCP")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as cliente:
        cliente.settimeout(10)

        log(f"Conectando con el servidor {HOST}:{PUERTO}")
        cliente.connect((HOST, PUERTO))

        mensaje = json.dumps(datos, ensure_ascii=False) + "\n"
        log(f"Enviando solicitud: {datos}")
        cliente.sendall(mensaje.encode("utf-8"))

        log("Esperando respuesta del servidor")
        buffer = b""
        while b"\n" not in buffer:
            parte = cliente.recv(4096)
            if not parte:
                break
            buffer += parte

    if not buffer:
        return {"ok": False, "mensaje": "El servidor no envió respuesta"}

    log("Respuesta recibida")
    return json.loads(buffer.decode("utf-8").strip())


def mostrar_respuesta(respuesta):
    print("\n--- Respuesta del servidor ---")
    print(f"Estado: {'OK' if respuesta.get('ok') else 'ERROR'}")
    print(f"Mensaje: {respuesta.get('mensaje')}")

    if "id_solicitud" in respuesta:
        print(f"Solicitud: #{respuesta['id_solicitud']}")

    if "worker" in respuesta:
        print(f"Procesada por: Worker {respuesta['worker']}")

    if "usuario" in respuesta:
        print(f"Usuario: {respuesta['usuario']}")

    if "ip" in respuesta:
        print(f"IP: {respuesta['ip']}")

    if "tareas" in respuesta:
        print("\nTareas disponibles:")
        for tarea in respuesta["tareas"]:
            print(f"- {tarea}")

    if "recorrido" in respuesta:
        print("\nRecorrido de la solicitud:")
        for paso in respuesta["recorrido"]:
            print(paso)


def registrar_usuario():
    usuario = input("Ingrese usuario: ")
    contrasena = input("Ingrese contraseña: ")

    respuesta = enviar_solicitud({
        "accion": "registro",
        "usuario": usuario,
        "contrasena": contrasena,
    })

    mostrar_respuesta(respuesta)


def iniciar_sesion():
    usuario = input("Ingrese usuario: ")
    contrasena = input("Ingrese contraseña: ")

    respuesta = enviar_solicitud({
        "accion": "login",
        "usuario": usuario,
        "contrasena": contrasena,
    })

    mostrar_respuesta(respuesta)


def ver_tareas():
    respuesta = enviar_solicitud({
        "accion": "tareas",
    })

    mostrar_respuesta(respuesta)


def cerrar_sesion():
    respuesta = enviar_solicitud({
        "accion": "logout",
    })

    mostrar_respuesta(respuesta)


def menu():
    while True:
        print("\n--- Cliente del Sistema de Tareas ---")
        print("1. Registrar usuario")
        print("2. Iniciar sesión")
        print("3. Ver página de tareas")
        print("4. Salir")

        opcion = input("Seleccione una opción: ")

        if opcion == "1":
            registrar_usuario()
        elif opcion == "2":
            iniciar_sesion()
        elif opcion == "3":
            ver_tareas()
        elif opcion == "4":
            cerrar_sesion()
            print("Saliendo...")
            break
        else:
            print("Opción inválida")


if __name__ == "__main__":
    menu()
