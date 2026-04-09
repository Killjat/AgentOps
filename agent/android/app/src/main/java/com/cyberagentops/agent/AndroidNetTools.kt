package com.cyberagentops.agent

import android.content.Context
import okhttp3.*
import org.json.JSONObject
import java.net.InetAddress
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

/**
 * Android 原生网络工具集
 * 替代 curl / traceroute / nslookup / ping 等 shell 命令
 * 服务端发来的命令先经过 intercept()，能处理的直接返回结果，不能处理的返回 null 走 shell
 */
object AndroidNetTools {

    private val httpClient: OkHttpClient by lazy {
        val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) {}
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        })
        val ssl = SSLContext.getInstance("TLS").apply { init(null, trustAll, java.security.SecureRandom()) }
        OkHttpClient.Builder()
            .sslSocketFactory(ssl.socketFactory, trustAll[0] as X509TrustManager)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .build()
    }

    /**
     * 拦截命令，能处理的返回结果字符串，不能处理的返回 null
     */
    fun intercept(command: String, context: Context? = null): String? {
        val cmd = command.trim()

        // ipinfo / curl ipinfo.io
        if (cmd.contains("ipinfo.io") || cmd == "ipinfo") {
            return fetchIpInfo()
        }

        // traceroute / tracepath / mtr
        if (cmd.startsWith("traceroute") || cmd.startsWith("tracepath") || cmd.startsWith("mtr")) {
            val target = extractTarget(cmd) ?: return "[Android] 无法解析目标"
            return simulateTraceroute(target)
        }

        // ping
        if (cmd.startsWith("ping")) {
            val target = extractTarget(cmd) ?: return "[Android] 无法解析目标"
            val count = extractPingCount(cmd)
            return doPing(target, count)
        }

        // nslookup / dig / host（含管道的也拦截，只取域名部分）
        if (cmd.startsWith("nslookup") || cmd.startsWith("dig") || cmd.startsWith("host ")) {
            val target = extractFirstArg(cmd) ?: return "[Android] 无法解析目标"
            return doNslookup(target)
        }

        // curl（含管道的也拦截）
        if (cmd.startsWith("curl") || cmd.contains("| curl") || cmd.contains("curl ")) {
            // 提取 curl 部分
            val curlPart = if (cmd.startsWith("curl")) cmd else cmd.substringAfter("curl ").let { "curl $it" }
            return handleCurl(curlPart.split("|")[0].trim())
        }

        // wget
        if (cmd.startsWith("wget")) {
            val url = cmd.split("\\s+".toRegex()).lastOrNull { it.startsWith("http") } ?: return null
            return doHttpGet(url)
        }

        // ifconfig / ip addr
        if (cmd.startsWith("ifconfig") || cmd == "ip addr" || cmd == "ip a" || cmd.startsWith("ip addr")) {
            return getNetworkInterfaces()
        }

        // DNS 泄露检测（含 dns.google 的命令）
        if (cmd.contains("dns.google")) {
            val urlMatch = Regex("name=([^&\\s]+)").find(cmd)
            val target = urlMatch?.groupValues?.get(1) ?: "tiktok.com"
            return dohQuery(target)
        }

        return null  // 不拦截，走 shell
    }

    // ── ipinfo ────────────────────────────────────────────────
    private fun fetchIpInfo(): String {
        return try {
            val req = Request.Builder().url("https://ipinfo.io/json").build()
            val resp = httpClient.newCall(req).execute()
            resp.body?.string() ?: "{}"
        } catch (e: Exception) {
            """{"error": "${e.message?.take(80)}"}"""
        }
    }

    // ── traceroute：用 /system/bin/ping TTL 递增实现（真正的路由追踪）────
    private fun simulateTraceroute(target: String): String {
        val sb = StringBuilder()
        return try {
            val targetAddr = InetAddress.getByName(target)
            sb.appendLine("traceroute to $target (${targetAddr.hostAddress}), 20 hops max")

            for (ttl in 1..20) {
                val raw = runPingTtl(target, ttl)
                val hopIp = extractHopIp(raw)
                val ms = extractPingMs(raw)

                if (hopIp != null) {
                    sb.appendLine(" $ttl  $hopIp  ${if (ms > 0) "${ms}ms" else "*"}")
                    if (hopIp == targetAddr.hostAddress) break  // 到达目标
                } else {
                    sb.appendLine(" $ttl  * * *")
                    // 连续3个超时且已超过5跳，停止
                    if (ttl > 5) {
                        val lastLines = sb.lines().takeLast(4)
                        if (lastLines.count { it.contains("* * *") } >= 3) break
                    }
                }
            }
            sb.toString()
        } catch (e: Exception) {
            "traceroute to $target\n[Android] ${e.message?.take(80)}"
        }
    }

    private fun runPingTtl(target: String, ttl: Int): String {
        return try {
            // Android 有 /system/bin/ping，-t 设置 TTL
            val proc = ProcessBuilder("ping", "-c", "1", "-t", "$ttl", "-W", "2", target)
                .redirectErrorStream(true)
                .start()
            proc.waitFor(4, TimeUnit.SECONDS)
            proc.inputStream.bufferedReader().readText().take(500)
        } catch (e: Exception) { "" }
    }

    private fun extractHopIp(pingOutput: String): String? {
        // "From 10.0.0.1: Time to live exceeded"
        val fromMatch = Regex("From ([\\d.]+)").find(pingOutput)
        if (fromMatch != null) return fromMatch.groupValues[1]
        // "64 bytes from 1.2.3.4: ..."（到达目标）
        val bytesMatch = Regex("bytes from ([\\d.]+)").find(pingOutput)
        return bytesMatch?.groupValues?.get(1)
    }

    private fun extractPingMs(pingOutput: String): Long {
        val m = Regex("time[=<]([\\d.]+)\\s*ms", setOf(RegexOption.IGNORE_CASE)).find(pingOutput)
            ?: Regex("([\\d.]+)\\s*ms").find(pingOutput)
        return m?.groupValues?.get(1)?.toDoubleOrNull()?.toLong() ?: -1L
    }

    // ── ping（用 /system/bin/ping，不用 isReachable）────────
    private fun doPing(target: String, count: Int): String {
        return try {
            val proc = ProcessBuilder("ping", "-c", "$count", "-W", "3", target)
                .redirectErrorStream(true)
                .start()
            proc.waitFor((count * 4 + 5).toLong(), TimeUnit.SECONDS)
            proc.inputStream.bufferedReader().readText().take(2000)
        } catch (e: Exception) {
            "ping: $target: ${e.message?.take(60)}"
        }
    }
    // ── nslookup ──────────────────────────────────────────────
    private fun doNslookup(target: String): String {
        return try {
            val addrs = InetAddress.getAllByName(target)
            buildString {
                appendLine("Server: (Android system DNS)")
                appendLine("Name: $target")
                addrs.forEach { appendLine("Address: ${it.hostAddress}") }
            }
        } catch (e: Exception) {
            "nslookup: $target: ${e.message?.take(60)}"
        }
    }

    // ── curl 拦截 ─────────────────────────────────────────────
    private fun handleCurl(cmd: String): String {
        // 提取 URL
        val urlRegex = Regex("https?://[^\\s'\"]+")
        val url = urlRegex.find(cmd)?.value ?: return "[Android curl] 无法解析 URL"

        // -w '%{time_total}' 延迟测试
        if (cmd.contains("-w") && cmd.contains("time_total")) {
            val ms = measureHttpLatency(url)
            return if (ms >= 0) "%.3f".format(ms / 1000.0) else "0.000"
        }

        // -o /dev/null -w '%{http_code}' 状态码
        if (cmd.contains("http_code")) {
            return try {
                val req = Request.Builder().url(url).head().build()
                val resp = httpClient.newCall(req).execute()
                resp.code.toString()
            } catch (e: Exception) { "000" }
        }

        // cloudflare trace
        if (url.contains("cloudflare.com/cdn-cgi/trace")) {
            return doHttpGet(url)
        }

        // 普通 GET
        return doHttpGet(url)
    }

    private fun doHttpGet(url: String): String {
        return try {
            val req = Request.Builder().url(url).build()
            val resp = httpClient.newCall(req).execute()
            resp.body?.string()?.take(5000) ?: ""
        } catch (e: Exception) {
            "[Android HTTP] ${e.message?.take(80)}"
        }
    }

    // ── Google DoH 查询 ───────────────────────────────────────
    private fun dohQuery(target: String): String {
        return try {
            val req = Request.Builder()
                .url("https://dns.google/resolve?name=$target&type=A")
                .header("Accept", "application/dns-json")
                .build()
            val resp = httpClient.newCall(req).execute()
            resp.body?.string() ?: "{}"
        } catch (e: Exception) {
            """{"error": "${e.message?.take(80)}"}"""
        }
    }

    // ── 网络接口信息 ──────────────────────────────────────────
    private fun getNetworkInterfaces(): String {
        return try {
            buildString {
                NetworkInterface.getNetworkInterfaces()?.toList()?.forEach { iface ->
                    if (!iface.isLoopback && iface.isUp) {
                        appendLine("${iface.name}:")
                        iface.inetAddresses?.toList()?.forEach { addr ->
                            appendLine("  inet ${addr.hostAddress}")
                        }
                    }
                }
            }.ifEmpty { "无网络接口信息" }
        } catch (e: Exception) { "获取网络接口失败: ${e.message}" }
    }

    // ── 工具函数 ──────────────────────────────────────────────
    private fun extractTarget(cmd: String): String? {
        // 去掉管道后面的部分，再取最后一个非 - 开头的 token
        val mainCmd = cmd.split("|")[0].trim()
        val tokens = mainCmd.split("\\s+".toRegex())
        return tokens.lastOrNull { !it.startsWith("-") && it != tokens[0] && !it.startsWith("/") && !it.startsWith("%") }
    }

    private fun extractFirstArg(cmd: String): String? {
        // 取命令名后的第一个非参数 token（用于 nslookup domain | grep ...）
        val mainCmd = cmd.split("|")[0].trim()
        val tokens = mainCmd.split("\\s+".toRegex()).drop(1)
        return tokens.firstOrNull { !it.startsWith("-") && it.isNotBlank() }
    }

    private fun extractPingCount(cmd: String): Int {
        val m = Regex("-c\\s*(\\d+)").find(cmd)
        return m?.groupValues?.get(1)?.toIntOrNull() ?: 4
    }

    private fun measureReachable(addr: InetAddress, timeoutMs: Int): Long {
        return try {
            val start = System.currentTimeMillis()
            val reachable = addr.isReachable(timeoutMs)
            if (reachable) System.currentTimeMillis() - start else -1L
        } catch (e: Exception) { -1L }
    }

    private fun measureHttpLatency(url: String): Long {
        return try {
            val start = System.currentTimeMillis()
            val req = Request.Builder().url(url).head().build()
            val resp = httpClient.newCall(req).execute()
            resp.close()
            System.currentTimeMillis() - start
        } catch (e: Exception) { -1L }
    }

    private fun readDefaultGateway(): String? {
        return try {
            val lines = java.io.File("/proc/net/route").readLines()
            val default = lines.drop(1).firstOrNull { it.split("\t").getOrNull(1) == "00000000" }
            val hex = default?.split("\t")?.getOrNull(2) ?: return null
            if (hex.length != 8) return null
            (0..3).map { i -> hex.substring(6 - i * 2, 8 - i * 2).toInt(16) }.joinToString(".")
        } catch (e: Exception) { null }
    }
}
