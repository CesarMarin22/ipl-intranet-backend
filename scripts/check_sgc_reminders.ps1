# =====================================================================
# Script para disparar el chequeo diario de vencimientos del SGC.
# Se ejecuta desde una Tarea Programada de Windows (Task Scheduler).
#
# Uso manual (para probar):
#   powershell -ExecutionPolicy Bypass -File check_sgc_reminders.ps1
# =====================================================================

# Ajusta esta URL a la dirección real donde corre tu API en producción.
$ApiUrl = "http://68.155.144.63:8083/api/sgc/documents/check-reminders"

# Debe coincidir EXACTAMENTE con SGC_CRON_SECRET en tu archivo .env
$CronSecret = "Iso9001correoenviado"

$Headers = @{ "X-Cron-Secret" = $CronSecret }

try {
    $response = Invoke-RestMethod -Uri $ApiUrl -Method POST -Headers $Headers -TimeoutSec 60
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$timestamp] OK - Enviados: $($response.data.enviados.Count) | Omitidos: $($response.data.omitidos.Count) | Errores: $($response.data.errores.Count)"

    if ($response.data.errores.Count -gt 0) {
        Write-Output "Errores detectados:"
        $response.data.errores | ForEach-Object { Write-Output " - Documento $($_.SGCID): $($_.error)" }
    }
} catch {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$timestamp] ERROR al llamar el endpoint de recordatorios: $($_.Exception.Message)"
}