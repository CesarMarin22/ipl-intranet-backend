"""Follow-up emails for Flash Reports, same content and sender as OTA's notificar_flash_reports."""

import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

NARANJA = "#F18700"
NEGRO_TEXTO = "#1c1c1c"


def _seccion(emoji, titulo, contenido):
    if not contenido:
        return ""
    return f"""
    <div style="margin:18pt 0;">
      <div style="color:{NARANJA};font-family:Arial;font-weight:700;font-size:14pt;margin-bottom:4pt;">
        {emoji} {titulo}
      </div>
      <div style="color:{NEGRO_TEXTO};font-family:Arial;font-size:11pt;line-height:1.5;">
        {contenido}
      </div>
    </div>
    """


def _plantilla(titulo, tamano_titulo, docnum, severidad, secciones, pie):
    etiqueta, color_fondo, color_texto = severidad
    return f"""
    <html><body style="margin:0;padding:0;background:#ffffff;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:800px;margin:auto;font-family:Arial, sans-serif;">
        <tr>
          <td align="center" style="padding:25px 0 10px 0;">
            <img src="cid:logo" width="144" style="display:block;" alt="IPL Inter Price Logística" />
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:10px;">
            <h1 style="margin:0;font-size:{tamano_titulo};font-family:Arial;font-weight:700;color:{NARANJA};">
              {titulo}
            </h1>
            <p style="margin:4px 0 0 0;color:#555;font-size:12pt;font-family:Arial;">
              Folio SAP {docnum}
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:0 30px 20px 30px;">
            <div style="text-align:center;margin:10px 0 20px 0;padding:10px 15px;border-radius:6px;
                        background:{color_fondo};color:{color_texto};font-weight:bold;font-size:13pt;
                        font-family:Arial;">
              Severidad: {etiqueta}
            </div>
            {"".join(_seccion(*s) for s in secciones)}
          </td>
        </tr>
        <tr>
          <td style="padding:20px 30px;color:#999;font-size:9pt;font-family:Arial;
                     text-align:center;border-top:1px solid #eee;">
            {pie}
          </td>
        </tr>
      </table>
    </body></html>
    """


def _enviar(asunto, html):
    usuario = os.getenv("FLASH_SMTP_USER")
    password = os.getenv("FLASH_SMTP_APP_PASSWORD")
    destinatarios = [c.strip() for c in os.getenv("FLASH_SMTP_TO_GROUP", "").split(",") if c.strip()]
    if not usuario or not password or not destinatarios:
        raise RuntimeError("Falta FLASH_SMTP_USER / FLASH_SMTP_APP_PASSWORD / FLASH_SMTP_TO_GROUP en el .env")

    msg = MIMEMultipart("related")
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))

    logo_path = os.getenv("FLASH_LOGO_PATH", "")
    if logo_path and os.path.exists(logo_path):
        subtipo = logo_path.rsplit(".", 1)[-1].lower().replace("jpg", "jpeg")
        with open(logo_path, "rb") as f:
            logo = MIMEImage(f.read(), _subtype=subtipo)
        logo.add_header("Content-ID", "<logo>")
        logo.add_header("Content-Disposition", "inline", filename=f"logo.{subtipo}")
        msg.attach(logo)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(usuario, password)
        server.sendmail(usuario, destinatarios, msg.as_string())


def enviar_correo_cierre(ot, severidad, sucursal, responsable, comentario, cerrado_por, fecha_cierre):
    docnum = ot.get("DocNum")
    html = _plantilla(
        "Flash Report Cerrado", "28pt", docnum, severidad,
        [
            ("🏢", "Sucursal", sucursal),
            ("❓", "Origen del Flash Report", ot.get("Subject") or ot.get("Description") or "N/A"),
            ("✅", "Cerrado por", cerrado_por),
            ("📅", "Fecha de cierre", fecha_cierre),
            ("👤", "Responsable del seguimiento", responsable or "N/A"),
            ("📝", "Comentario final", comentario or "Sin comentarios"),
        ],
        "Correo generado automáticamente al cerrar el seguimiento del Flash Report.",
    )
    _enviar(f"✅ Flash Report Cerrado (Folio SAP {docnum})", html)


def enviar_correo_actualizacion(ot, severidad, sucursal, estatus, responsable, fecha_compromiso,
                                comentario, creado_por, creado_en):
    docnum = ot.get("DocNum")
    html = _plantilla(
        "Actualización de Seguimiento", "26pt", docnum, severidad,
        [
            ("❓", "Origen del Flash Report", ot.get("Subject") or ot.get("Description") or "N/A"),
            ("📋", "Nuevo Estatus", estatus),
            ("🏢", "Sucursal", sucursal),
            ("👤", "Responsable", responsable or "N/A"),
            ("📅", "Fecha Compromiso", fecha_compromiso or "N/A"),
            ("📝", "Comentario", comentario or "Sin comentarios"),
            ("🖊️", "Registrado por", f"{creado_por} — {creado_en}"),
        ],
        "Correo generado automáticamente al registrar un seguimiento del Flash Report.",
    )
    _enviar(f"📋 Seguimiento actualizado (Folio SAP {docnum}) - Estatus: {estatus}", html)
