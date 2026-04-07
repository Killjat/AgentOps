package com.cyberagentops.agent

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.StatFs
import android.os.SystemClock
import android.provider.Settings
import org.json.JSONObject
import java.io.BufferedReader
import java.io.FileReader
import java.net.NetworkInterface
import java.text.SimpleDateFormat
import java.util.*

data class DeviceBasicInfo(
    val model: String,
    val brand: String,
    val osVersion: String,
    val hostname: String
)

object DeviceInfo {

    fun get(context: Context) = DeviceBasicInfo(
        model = Build.MODEL,
        brand = Build.BRAND,
        osVersion = "Android ${Build.VERSION.RELEASE}",
        hostname = Build.MODEL  // bluetooth_name 在 Android 12+ 需要特殊权限，直接用型号名
    )

    fun getMetrics(context: Context): JSONObject {
        val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        sdf.timeZone = TimeZone.getTimeZone("UTC")
        
        return JSONObject().apply {
            put("timestamp", sdf.format(Date()))
            put("agent_id", AgentConfig.getAgentId(context))
            put("os_info", JSONObject().apply {
                put("os", "Android")
                put("os_version", "Android ${Build.VERSION.RELEASE}")
                put("model", Build.MODEL)
                put("brand", Build.BRAND)
                put("hostname", get(context).hostname)
            })
            put("cpu_usage", getCpuUsage())
            put("disk", getDiskInfo())
            put("network", getNetworkInfo(context))
            put("hardware", JSONObject().apply {
                put("cpu_model", Build.HARDWARE)
                put("cpu_cores", Runtime.getRuntime().availableProcessors())
                put("memory_mb", getTotalMemoryMb())
                put("board_name", Build.MODEL)
                // 修复：避免直接访问 Build.SERIAL 导致安全异常
                put("board_serial", "unknown") 
                put("hw_fingerprint", Build.FINGERPRINT.take(16))
            })
        }
    }

    private fun getCpuUsage(): Double {
        // 优先用 top 命令获取系统级 CPU 使用率
        return try {
            val result = CommandExecutor.exec("top -n 1 -b 2>/dev/null | grep -E '^[0-9]+%cpu'", 5)
            if (result.success && result.output.isNotBlank()) {
                // 格式: 800%cpu  20%user  720%idle ...
                val idleMatch = Regex("(\\d+)%idle").find(result.output)
                val totalMatch = Regex("(\\d+)%cpu").find(result.output)
                if (idleMatch != null && totalMatch != null) {
                    val idle = idleMatch.groupValues[1].toDouble()
                    val total = totalMatch.groupValues[1].toDouble()
                    if (total > 0) return Math.round((100.0 - idle / total * 100) * 10) / 10.0
                }
            }
            // fallback: /proc/stat
            val stat1 = readCpuStat()
            if (stat1.size >= 4) {
                SystemClock.sleep(300)
                val stat2 = readCpuStat()
                if (stat2.size >= 4) {
                    val idle = stat2[3] - stat1[3]
                    val total = stat2.sum() - stat1.sum()
                    if (total > 0) return Math.round(100.0 * (1 - idle.toDouble() / total) * 10) / 10.0
                }
            }
            -1.0
        } catch (e: Exception) { -1.0 }
    }

    private fun readCpuStat(): LongArray {
        return try {
            val line = BufferedReader(FileReader("/proc/stat")).use { it.readLine() }
            line.trim().split("\\s+".toRegex()).drop(1).take(7).map { it.toLong() }.toLongArray()
        } catch (e: Exception) { longArrayOf() }
    }

    private fun getTotalMemoryMb(): Long {
        return try {
            val line = BufferedReader(FileReader("/proc/meminfo")).use { it.readLine() }
            line.trim().split("\\s+".toRegex())[1].toLong() / 1024
        } catch (e: Exception) { 0L }
    }

    private fun getDiskInfo(): JSONObject {
        return try {
            val stat = StatFs("/data")
            val total = stat.totalBytes / (1024 * 1024)
            val free = stat.availableBytes / (1024 * 1024)
            val used = total - free
            JSONObject().apply {
                put("mount", "/data")
                put("size", "${total}M")
                put("used", "${used}M")
                put("avail", "${free}M")
                put("use_pct", "${if (total > 0) used * 100 / total else 0}%")
            }
        } catch (e: Exception) { JSONObject() }
    }

    private fun getNetworkInfo(context: Context): JSONObject {
        val result = JSONObject()
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val network = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) cm.activeNetwork else null
            val caps = if (network != null) cm.getNetworkCapabilities(network) else null
            val type = when {
                caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true -> "WiFi"
                caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true -> "Mobile"
                else -> "Unknown"
            }
            result.put("type", type)

            NetworkInterface.getNetworkInterfaces()?.toList()?.forEach { iface ->
                iface.inetAddresses?.toList()?.forEach { addr ->
                    if (!addr.isLoopbackAddress && addr.hostAddress?.contains(':') == false) {
                        result.put("eth", addr.hostAddress)
                    }
                }
            }
        } catch (e: Exception) {}
        return result
    }
}
