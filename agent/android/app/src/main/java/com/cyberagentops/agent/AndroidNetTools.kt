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

    // ── traceroute 模拟（TTL 递增 + InetAddress）─────────────
    private fun simulateTraceroute(target: String): String {
        val sb = StringBuilder()
        return try {
            val targetAddr = InetAddress.getByName(target)
            sb.appendLine("traceroute to $target (${targetAddr.hostAddress}), 20 hops max")

            // 读网关（/proc/net/route）
            val gateway = readDefaultGateway()
            if (gateway != null) {
                val ms = measureReachable(InetAddress.getByName(gateway), 500)
                sb.appendLine(" 1  $gateway  ${if (ms >= 0) "${ms}ms" else "*"}")
            }

            // TTL 递增探测（用 isReachable 近似）
            val timeouts = listOf(50, 100, 200, 400, 800, 1500, 3000, 5000)
            var prevMs = -1L
            for ((hop, timeout) in timeouts.withIndex()) {
                val ms = measureReachable(targetAddr, timeout)
                if (ms >= 0 && ms != prevMs) {
                    val hopNum = (gateway?.let { 2 } ?: 1) + hop
                    sb.appendLine(" $hopNum  ${targetAddr.hostAddress}  ${ms}ms")
                    prevMs = ms
                    if (hop >= 3) break  // 到达目标后停止
                } else if (hop < 3) {
                    val hopNum = (gateway?.let { 2 } ?: 1) + hop
                    sb.appendLine(" $hopNum  *")
                }
            }

            // 最终延迟
            val finalMs = measureHttpLatency("https://$target")
            if (finalMs >= 0) {
                sb.appendLine("\n目标 HTTP 延迟: ${finalMs}ms")
            }

            sb.toString()
        } catch (e: Exception) {
            "[Android traceroute] 目标: $target\n错误: ${e.message?.take(80)}\n" +
            "注：Android 无原生 traceroute，使用 ICMP reachable 模拟"
        }
    }

    // ── ping ──────────────────────────────────────────────────
    private fun doPing(target: String, count: Int): String {
        val sb = StringBuilder()
        return try {
            val addr = InetAddress.getByName(target)
            sb.appendLine("PING $target (${addr.hostAddress})")
            val results = mutableListOf<Long>()
            repeat(count) { i ->
                val ms = measureReachable(addr, 3000)
                if (ms >= 0) {
                    results.add(ms)
                    sb.appendLine("64 bytes from ${addr.hostAddress}: icmp_seq=$i ttl=64 time=${ms}ms")
                } else {
                    sb.appendLine("Request timeout for icmp_seq $i")
                }
                if (i < count - 1) Thread.sleep(500)
            }
            val loss = ((count - results.size).toDouble() / count * 100).toInt()
            sb.appendLine("\n--- $target ping statistics ---")
            sb.appendLine("$count packets transmitted, ${results.size} received, $loss% packet loss")
            if (results.isNotEmpty()) {
                sb.appendLine("rtt min/avg/max = ${results.min()}/${results.average().toLong()}/${results.max()} ms")
            }
            sb.toString()
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
