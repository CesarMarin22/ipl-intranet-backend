import os
import smtplib
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _smtp_config():
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from_email": os.getenv("SMTP_FROM", os.getenv("SMTP_USER")),
        "from_name": os.getenv("SMTP_FROM_NAME", "SGC - Inter Price Logística"),
        "use_tls": os.getenv("SMTP_USE_TLS", "1") != "0",
    }


def send_email(to_email, subject, html_body, text_body=None):
    """
    Envía un correo HTML usando SMTP (Gmail / Google Workspace por defecto).
    Devuelve (ok: bool, error: str | None). No lanza excepción.
    """
    config = _smtp_config()

    if not to_email:
        return False, "No hay correo destinatario configurado"

    if not config["user"] or not config["password"]:
        return False, "SMTP no está configurado (revisa SMTP_USER / SMTP_PASSWORD en .env)"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f'{config["from_name"]} <{config["from_email"]}>'
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(config["host"], config["port"], timeout=20) as server:
            if config["use_tls"]:
                server.starttls()
            server.login(config["user"], config["password"])
            server.sendmail(config["from_email"], [to_email], msg.as_string())

        return True, None
    except Exception as exc:
        traceback.print_exc()
        return False, str(exc)


def _wrapper(color, encabezado, saludo, cuerpo_tabla_html, mensaje_final):
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      <div style="background:{color}; color:#fff; padding:16px 20px; border-radius:8px 8px 0 0;">
        <h2 style="margin:0; font-size:18px;">{encabezado}</h2>
      </div>
      <div style="border:1px solid #E5E7EB; border-top:none; padding:20px; border-radius:0 0 8px 8px;">
        <p>{saludo}</p>
        <table style="width:100%; border-collapse:collapse; margin:16px 0;">
          {cuerpo_tabla_html}
        </table>
        <p>{mensaje_final}</p>
        <p style="color:#9CA3AF; font-size:12px; margin-top:24px;">
          Este es un correo automático del sistema SGC de Inter Price Logística. No respondas a este mensaje.
        </p>
      </div>
    </div>
    """
    return html_body


def _fila(label, valor, color_valor="#111827"):
    return f'<tr><td style="padding:4px 0; color:#6B7280;">{label}:</td><td style="padding:4px 0; font-weight:bold; color:{color_valor};">{valor}</td></tr>'


# ----------------------------------------------------------------
# Recordatorio de vencimiento (documento por vencer / vencido)
# ----------------------------------------------------------------

def build_sgc_reminder_email(documento, estado, dias):
    titulo = documento.get("TITULO") or "Documento"
    codigo = documento.get("CODIGO") or "-"
    fecha_limite = documento.get("FECHA_LIMITE")
    responsable = documento.get("RESPONSABLE_NOMBRE") or ""

    if estado == "VENCIDO":
        asunto = f"[SGC] Documento VENCIDO: {titulo}"
        encabezado = "Este documento ya está vencido"
        color = "#DC2626"
        mensaje_dias = f"Venció hace {abs(dias)} día(s)."
    elif estado == "URGENTE":
        asunto = f"[SGC] Urgente: {titulo} vence en {dias} día(s)"
        encabezado = "Este documento está por vencer (urgente)"
        color = "#EA580C"
        mensaje_dias = f"Vence en {dias} día(s)."
    else:
        asunto = f"[SGC] Próximo a vencer: {titulo} ({dias} días)"
        encabezado = "Este documento está próximo a vencer"
        color = "#D97706"
        mensaje_dias = f"Vence en {dias} día(s)."

    tabla = (
        _fila("Código", codigo)
        + _fila("Título", titulo)
        + _fila("Fecha límite", fecha_limite)
        + _fila("Estado", mensaje_dias, color)
    )

    html_body = _wrapper(
        color, encabezado, f"Hola {responsable},",
        tabla, "Por favor revisa y actualiza este documento según corresponda."
    )
    text_body = f"{encabezado}\n\nCódigo: {codigo}\nTítulo: {titulo}\nFecha límite: {fecha_limite}\n{mensaje_dias}\n"

    return asunto, html_body, text_body


# ----------------------------------------------------------------
# Aviso a Calidad: hay un documento/versión pendiente de autorizar
# ----------------------------------------------------------------

def build_pending_approval_email(documento, version, es_nueva_version=False):
    titulo = documento.get("TITULO") or "Documento"
    codigo = documento.get("CODIGO") or "-"
    responsable = documento.get("RESPONSABLE_NOMBRE") or "-"
    numero_version = version.get("NUMERO_VERSION")

    accion = "una nueva versión de" if es_nueva_version else "un documento nuevo"
    asunto = f"[SGC] Pendiente de autorizar: {titulo} (v{numero_version})"
    encabezado = "Hay un documento pendiente de tu autorización"

    tabla = (
        _fila("Código", codigo)
        + _fila("Título", titulo)
        + _fila("Versión", f"v{numero_version}")
        + _fila("Subido por", responsable)
    )

    html_body = _wrapper(
        "#2563EB", encabezado,
        f"Se subió {accion} <b>{titulo}</b> y está esperando tu revisión.",
        tabla,
        "Entra al sistema SGC para revisar el archivo y autorizarlo o rechazarlo."
    )
    text_body = f"{encabezado}\n\nCódigo: {codigo}\nTítulo: {titulo}\nVersión: v{numero_version}\nSubido por: {responsable}\n"

    return asunto, html_body, text_body


# ----------------------------------------------------------------
# Aviso al Responsable: su documento fue autorizado o rechazado
# ----------------------------------------------------------------

def build_decision_email(documento, version, aprobado, comentarios=None):
    titulo = documento.get("TITULO") or "Documento"
    codigo = documento.get("CODIGO") or "-"
    numero_version = version.get("NUMERO_VERSION")

    if aprobado:
        asunto = f"[SGC] Autorizado: {titulo} (v{numero_version})"
        encabezado = "Tu documento fue autorizado y liberado"
        color = "#16A34A"
        mensaje_final = "Esta versión ya está disponible como la versión vigente del documento."
    else:
        asunto = f"[SGC] Rechazado: {titulo} (v{numero_version})"
        encabezado = "Tu documento fue rechazado"
        color = "#DC2626"
        mensaje_final = "Corrige lo indicado y sube una nueva versión para volver a enviarla a revisión."

    tabla = (
        _fila("Código", codigo)
        + _fila("Título", titulo)
        + _fila("Versión", f"v{numero_version}")
    )

    if comentarios:
        tabla += _fila("Comentarios de Calidad", comentarios)

    html_body = _wrapper(color, encabezado, "Hola,", tabla, mensaje_final)
    text_body = f"{encabezado}\n\nCódigo: {codigo}\nTítulo: {titulo}\nVersión: v{numero_version}\n" + (f"Comentarios: {comentarios}\n" if comentarios else "")

    return asunto, html_body, text_body