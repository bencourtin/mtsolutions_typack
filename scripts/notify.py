"""
Módulo de notificaciones por email para el ETL Typack.

Soporta dos tipos de notificaciones:
1. Alertas de error inmediatas (cuando falla extracción o transformación)
2. Reportes semanales con estadísticas de ejecución

Configuración: .env (SMTP_*, EMAIL_*)
"""

import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Dict, Optional

from config.settings import EMAIL_CONFIG, PATHS


def _create_smtp_connection():
    """Crea conexión SMTP con la configuración de settings."""
    smtp_config = EMAIL_CONFIG.get("smtp", {})
    server = smtp_config.get("server", "smtp.gmail.com")
    port = smtp_config.get("port", 587)
    use_tls = smtp_config.get("use_tls", True)
    username = smtp_config.get("username", "")
    password = smtp_config.get("password", "")
    
    if use_tls:
        context = ssl.create_default_context()
        smtp_server = smtplib.SMTP(server, port)
        smtp_server.starttls(context=context)
    else:
        smtp_server = smtplib.SMTP(server, port)
    
    if username and password:
        smtp_server.login(username, password)
    
    return smtp_server


def _send_email(
    recipients: List[str],
    subject: str,
    body_html: str,
    body_text: str = None
) -> bool:
    """
    Envía email a múltiples destinatarios.
    
    Args:
        recipients: Lista de direcciones de email
        subject: Asunto del email
        body_html: Cuerpo en formato HTML
        body_text: Cuerpo en texto plano (opcional, se genera desde HTML si no se proporciona)
    
    Returns:
        True si se envió correctamente, False en caso contrario
    """
    if not EMAIL_CONFIG.get("enabled", False):
        print("Notificaciones por email deshabilitadas en config.")
        return False
    
    sender_config = EMAIL_CONFIG.get("sender", {})
    sender_email = sender_config.get("email", "")
    sender_name = sender_config.get("name", "ETL Typack")
    
    if not sender_email or not recipients:
        print("Configuración de email incompleta.")
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = ", ".join(recipients)
        
        # Texto plano como fallback
        if body_text is None:
            body_text = body_html.replace("<br>", "\n").replace("</p>", "\n")
            import re
            body_text = re.sub(r'<[^>]+>', '', body_text)
        
        part1 = MIMEText(body_text, "plain", "utf-8")
        part2 = MIMEText(body_html, "html", "utf-8")
        
        msg.attach(part1)
        msg.attach(part2)
        
        with _create_smtp_connection() as server:
            server.sendmail(sender_email, recipients, msg.as_string())
        
        print(f"Email enviado a: {', '.join(recipients)}")
        return True
        
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False


def send_error_alert(
    error_type: str,
    error_message: str,
    details: Dict = None
) -> bool:
    """
    Envía alerta de error inmediata.
    
    Args:
        error_type: Tipo de error ("extractor" o "transformer")
        error_message: Mensaje de error principal
        details: Diccionario con detalles adicionales (opcional)
    
    Returns:
        True si se envió correctamente
    """
    recipients = EMAIL_CONFIG.get("recipients", {}).get("error", [])
    
    if not recipients:
        print("No hay destinatarios configurados para alertas de error.")
        return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Construir detalles HTML
    details_html = ""
    if details:
        details_html = "<h3>Detalles adicionales:</h3><ul>"
        for key, value in details.items():
            details_html += f"<li><strong>{key}:</strong> {value}</li>"
        details_html += "</ul>"
    
    subject = f" ERROR ETL Typack - {error_type.upper()}"
    
    body_html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .error-box {{ 
                background-color: #ffebee; 
                border-left: 4px solid #f44336; 
                padding: 15px; 
                margin: 20px 0;
            }}
            .timestamp {{ color: #666; font-size: 12px; }}
            h2 {{ color: #d32f2f; }}
        </style>
    </head>
    <body>
        <h2>⚠️ Error en ETL Typack</h2>
        <p class="timestamp">Fecha/Hora: {timestamp}</p>
        
        <div class="error-box">
            <p><strong>Componente:</strong> {error_type.upper()}</p>
            <p><strong>Error:</strong></p>
            <pre>{error_message}</pre>
        </div>
        
        {details_html}
        
        <hr>
        <p style="color: #666; font-size: 11px;">
            Este es un mensaje automático del sistema ETL Typack.
            Por favor revise los logs para más información.
        </p>
    </body>
    </html>
    """
    
    return _send_email(recipients, subject, body_html)

def send_weekly_report(stats: Dict) -> bool:
    """
    Envía reporte semanal con estadísticas de ejecución.
    
    Args:
        stats: Diccionario con estadísticas de la semana:
            - total_executions: Número total de ejecuciones
            - successful_executions: Ejecuciones exitosas
            - failed_executions: Ejecuciones fallidas
            - total_rows_extracted: Total de filas extraídas
            - total_rows_transformed: Total de filas transformadas
            - lines_without_data: Lista de líneas sin datos
            - execution_dates: Lista de fechas de ejecución
            - errors: Lista de errores ocurridos
    
    Returns:
        True si se envió correctamente
    """
    recipients = EMAIL_CONFIG.get("recipients", {}).get("weekly_report", [])
    
    if not recipients:
        print("No hay destinatarios configurados para reporte semanal.")
        return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    week_start = stats.get("week_start", "N/A")
    week_end = stats.get("week_end", "N/A")
    
    total_exec = stats.get("total_executions", 0)
    success_exec = stats.get("successful_executions", 0)
    failed_exec = stats.get("failed_executions", 0)
    total_extracted = stats.get("total_rows_extracted", 0)
    total_transformed = stats.get("total_rows_transformed", 0)
    lines_no_data = stats.get("lines_without_data", [])
    errors_list = stats.get("errors", [])
    
    # Calcular tasa de éxito
    success_rate = (success_exec / total_exec * 100) if total_exec > 0 else 0
    
    # Construir tabla de líneas sin datos
    lines_no_data_html = ""
    if lines_no_data:
        lines_no_data_html = """
        <h3>⚠️ Líneas sin datos esta semana:</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
            <tr style="background-color: #fff3e0;">
                <th>Línea</th>
                <th>Días sin datos</th>
            </tr>
        """
        for line_info in lines_no_data:
            lines_no_data_html += f"""
            <tr>
                <td>{line_info.get('line', 'N/A')}</td>
                <td>{line_info.get('days_count', 0)}</td>
            </tr>
            """
        lines_no_data_html += "</table>"
    else:
        lines_no_data_html = "<p> Todas las líneas tuvieron datos esta semana.</p>"
    
    # Construir lista de errores
    errors_html = ""
    if errors_list:
        errors_html = """
        <h3>❌ Errores registrados:</h3>
        <ul style="color: #d32f2f;">
        """
        for error in errors_list[-10:]:  # Limitar a últimos 10 errores
            errors_html += f"<li>{error}</li>"
        errors_html += "</ul>"
    
    # Determinar estado general
    if success_rate >= 95:
        status_emoji = "✅"
        status_text = "EXCELENTE"
        status_color = "#4caf50"
    elif success_rate >= 80:
        status_emoji = "⚠️"
        status_text = "ACEPTABLE"
        status_color = "#ff9800"
    else:
        status_emoji = "❌"
        status_text = "CRÍTICO"
        status_color = "#f44336"
    
    # Construir asunto del email
    # Solo adjuntar emoji y estado si hubo un error
    if success_rate < 100:
        subject = f" Reporte Semanal ETL Typack - {status_emoji} {status_text}"
    else:
        subject = " Reporte Semanal ETL Typack"
    
    body_html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .header {{ 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 20px;
                border-radius: 10px 10px 0 0;
            }}
            .content {{ padding: 20px; }}
            .stats-grid {{ 
                display: grid; 
                grid-template-columns: repeat(3, 1fr); 
                gap: 15px;
                margin: 20px 0;
            }}
            .stat-box {{
                background: #f5f5f5;
                padding: 15px;
                border-radius: 8px;
                text-align: center;
            }}
            .stat-number {{ font-size: 28px; font-weight: bold; color: #333; }}
            .stat-label {{ font-size: 12px; color: #666; }}
            .status-badge {{
                background-color: {status_color};
                color: white;
                padding: 5px 15px;
                border-radius: 20px;
                display: inline-block;
            }}
            table {{ width: 100%; margin: 15px 0; }}
            th {{ background-color: #667eea; color: white; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Reporte Semanal ETL Typack</h1>
            <p>Período: {week_start} - {week_end}</p>
            <span class="status-badge">{status_emoji} {status_text}</span>
        </div>
        
        <div class="content">
            <h2>Resumen de Ejecuciones</h2>
            
            <table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse;">
                <tr style="background-color: #667eea; color: white;">
                    <th>Métrica</th>
                    <th>Valor</th>
                </tr>
                <tr>
                    <td>Total de ejecuciones</td>
                    <td><strong>{total_exec}</strong></td>
                </tr>
                <tr>
                    <td>Ejecuciones exitosas</td>
                    <td style="color: #4caf50;"><strong>{success_exec}</strong></td>
                </tr>
                <tr>
                    <td>Ejecuciones fallidas</td>
                    <td style="color: #f44336;"><strong>{failed_exec}</strong></td>
                </tr>
                <tr>
                    <td>Tasa de éxito</td>
                    <td><strong>{success_rate:.1f}%</strong></td>
                </tr>
                <tr>
                    <td>Filas extraídas (total)</td>
                    <td><strong>{total_extracted:,}</strong></td>
                </tr>
                <tr>
                    <td>Filas transformadas (total)</td>
                    <td><strong>{total_transformed:,}</strong></td>
                </tr>
            </table>
            
            {lines_no_data_html}
            
            {errors_html}
            
            <hr>
            <p style="color: #666; font-size: 11px;">
                Generado automáticamente: {timestamp}<br>
                Este es un reporte automático del sistema ETL Typack.
            </p>
        </div>
    </body>
    </html>
    """
    
    return _send_email(recipients, subject, body_html)

def send_test_email() -> bool:
    """Envía un email de prueba para verificar la configuración."""
    recipients = EMAIL_CONFIG.get("recipients", {}).get("error", [])
    
    if not recipients:
        print("No hay destinatarios configurados.")
        return False
    
    subject = "Test de Notificaciones ETL Typack"
    body_html = """
    <html>
    <body>
        <h2>Configuración de Email Correcta</h2>
        <p>Este es un mensaje de prueba del sistema de notificaciones ETL Typack.</p>
        <p>Si recibiste este email, la configuración es correcta.</p>
    </body>
    </html>
    """
    
    return _send_email(recipients, subject, body_html)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("Enviando email de prueba...")
        success = send_test_email()
        sys.exit(0 if success else 1)
    
    elif len(sys.argv) > 1 and sys.argv[1] == "--weekly-report":
        print("Generando y enviando reporte semanal...")
        from scripts.stats_tracker import StatsTracker
        
        tracker = StatsTracker()
        stats = tracker.get_weekly_stats()
        
        if stats:
            success = send_weekly_report(stats)
            if success:
                print("Reporte semanal enviado exitosamente.")
            else:
                print("Error enviando reporte semanal.")
            sys.exit(0 if success else 1)
        else:
            print("No hay estadísticas disponibles para generar el reporte.")
            sys.exit(1)
    
    else:
        print("Uso:")
        print("  python notify.py --test           # Enviar email de prueba")
        print("  python notify.py --weekly-report  # Enviar reporte semanal")
