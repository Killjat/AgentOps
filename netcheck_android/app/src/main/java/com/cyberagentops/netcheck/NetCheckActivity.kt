package com.cyberagentops.netcheck

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONObject
import java.net.InetAddress
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

class NetCheckActivity : AppCompatActivity() {

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var running = false
    private var stopFlag = false

    private var count = 0
    private var lossCount = 0
    private val latencies = mutableListOf<Double>()

    // OkHttp client（信任所有证书，用于访问自签名服务器）
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
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .build()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_netcheck)

        val btnStart = findViewById<Button>(R.id.btnStartCheck)
        val btnStop  = findViewById<Button>(R.id.btnStopCheck)
        val etTarget = findViewById<EditText>(R.id.etTarget)

        btnStart.setOnClickListener {
            val target = etTarget.text.toString().trim().ifEmpty { "tiktok.com" }
            startCheck(target, btnStart, btnStop)
        }
        btnStop.setOnClickListener {
            stopFlag = true
            running = false
            btnStop.visibility = View.GONE
            btnStart.isEnabled = true
        }

        // 自动开始检测
        startCheck("tiktok.com", btnStart, btnStop)
    }

    private fun startCheck(target: String, btnStart: Button, btnStop: Button) {
        if (running) return
        running = true; stopFlag = false
        count = 0; lossCount = 0; latencies.clear()
        btnStart.isEnabled = false
        btnStop.visibility = View.VISIBLE

        val tvVerdict = findViewById<TextView>(R.id.tvVerdict)
        val tvStats   = findViewById<TextView>(R.id.tvStats)
        val tvLog     = findViewById<TextView>(R.id.tvLog)
        val tvIpInfo  = findViewById<TextView>(R.id.tvIpInfo)
        tvLog.text = ""; tvVerdict.text = "检测中..."; tvStats.text = "--"

        scope.launch {
            // 1. 出口 IP 信息（OkHttp，不用 curl）
            log(tvLog, "🌐 获取出口 IP...")
            val ipInfo = withContext(Dispatchers.IO) { fetchIpInfo() }
            tvIpInfo.text = buildIpInfoText(ipInfo)
            log(tvLog, "✅ IP: ${ipInfo?.optString("ip") ?: "获取失败"}\n")

            // 2. DNS 泄露检测（Java 原生 DNS + Google DoH via OkHttp）
            log(tvLog, "🔍 DNS 泄露检测...")
            val dnsResult = withContext(Dispatchers.IO) { checkDnsLeak(target) }
            log(tvLog, dnsResult + "\n")

            // 3. 出口分流检测（访问 Cloudflare trace，纯 HTTP）
            log(tvLog, "🔀 出口分流检测...")
            val splitResult = withContext(Dispatchers.IO) { checkOutboundSplit(ipInfo?.optString("ip") ?: "") }
            log(tvLog, splitResult + "\n")

            // 4. 路由模拟（TTL ping，Android 原生支持 InetAddress.isReachable）
            log(tvLog, "📡 路由路径探测...")
            val trResult = withContext(Dispatchers.IO) { simulateTraceroute(target) }
            log(tvLog, trResult + "\n")

            // 5. 持续延迟测试（OkHttp HEAD 请求，不用 curl）
            log(tvLog, "⏱️ 延迟测试（20次）...")
            repeat(20) { i ->
                if (stopFlag) return@repeat
                val ms = withContext(Dispatchers.IO) { measureLatency(target) }
                count++
                if (ms < 0) {
                    lossCount++
                    log(tvLog, "[$i] ❌ 超时")
                } else {
                    latencies.add(ms)
                    log(tvLog, "[$i] ${ms.toInt()}ms")
                }
                updateStats(tvStats, tvVerdict, ipInfo)
                delay(2000)
            }

            running = false
            btnStart.isEnabled = true
            btnStop.visibility = View.GONE
            log(tvLog, "\n✅ 检测完成")
        }
    }

    // ── 出口 IP 查询（OkHttp，不依赖 curl）────────────────────
    private fun fetchIpInfo(): JSONObject? {
        return try {
            val req = Request.Builder().url("https://ipinfo.io/json").build()
            val resp = httpClient.newCall(req).execute()
            if (resp.isSuccessful) JSONObject(resp.body?.string() ?: "") else null
        } catch (e: Exception) { null }
    }

    // ── DNS 泄露检测（Java 原生 + Google DoH via OkHttp）──────
    private fun checkDnsLeak(target: String): String {
        return try {
            // 本机 DNS 解析（Java 原生，不用 nslookup）
            val localIps = InetAddress.getAllByName(target).map { it.hostAddress }.take(3)

            // Google DoH（OkHttp，不用 curl）
            val req = Request.Builder()
                .url("https://dns.google/resolve?name=$target&type=A")
                .header("Accept", "application/dns-json")
                .build()
            val resp = httpClient.newCall(req).execute()
            val dohIps = mutableListOf<String>()
            if (resp.isSuccessful) {
                val json = JSONObject(resp.body?.string() ?: "{}")
                val answers = json.optJSONArray("Answer")
                if (answers != null) {
                    for (i in 0 until answers.length()) {
                        val a = answers.getJSONObject(i)
                        if (a.optInt("type") == 1) dohIps.add(a.optString("data"))
                    }
                }
            }

            val leaked = localIps.isNotEmpty() && dohIps.isNotEmpty() &&
                    localIps.none { it in dohIps }

            buildString {
                append("本机 DNS: ${localIps.joinToString(", ")}\n")
                append("Google DNS: ${dohIps.take(3).joinToString(", ")}\n")
                if (leaked) append("⚠️ DNS 泄露！代理未接管 DNS")
                else append("✅ DNS 正常，无泄露")
            }
        } catch (e: Exception) {
            "DNS 检测失败: ${e.message?.take(60)}"
        }
    }

    // ── 出口分流检测（Cloudflare trace，OkHttp）───────────────
    private fun checkOutboundSplit(mainIp: String): String {
        return try {
            val req = Request.Builder().url("https://cloudflare.com/cdn-cgi/trace").build()
            val resp = httpClient.newCall(req).execute()
            val body = resp.body?.string() ?: ""
            val cfIp = body.lines().firstOrNull { it.startsWith("ip=") }?.removePrefix("ip=")?.trim() ?: ""
            when {
                cfIp.isEmpty() -> "出口分流：无法获取 Cloudflare 出口"
                mainIp.isNotEmpty() && cfIp != mainIp ->
                    "⚠️ 出口分流：主出口 $mainIp ≠ Cloudflare $cfIp"
                else -> "✅ 出口一致：$cfIp"
            }
        } catch (e: Exception) {
            "出口分流检测失败: ${e.message?.take(50)}"
        }
    }

    // ── 路由追踪（用 /system/bin/ping TTL 递增，真正的 traceroute）────
    private fun simulateTraceroute(target: String): String {
        val sb = StringBuilder("路由追踪 to $target:\n")
        return try {
            val targetAddr = InetAddress.getByName(target)
            sb.append("目标 IP: ${targetAddr.hostAddress}\n\n")

            for (ttl in 1..15) {
                val proc = ProcessBuilder("ping", "-c", "1", "-t", "$ttl", "-W", "2", target)
                    .redirectErrorStream(true).start()
                proc.waitFor(4, TimeUnit.SECONDS)
                val raw = proc.inputStream.bufferedReader().readText()

                // 提取跳点 IP
                val hopIp = Regex("From ([\\d.]+)").find(raw)?.groupValues?.get(1)
                    ?: Regex("bytes from ([\\d.]+)").find(raw)?.groupValues?.get(1)
                val ms = Regex("time[=<]([\\d.]+)\\s*ms", setOf(RegexOption.IGNORE_CASE)).find(raw)
                    ?.groupValues?.get(1)?.toDoubleOrNull()?.toLong() ?: -1L

                if (hopIp != null) {
                    sb.append(" $ttl  $hopIp  ${if (ms > 0) "${ms}ms" else "*"}\n")
                    if (hopIp == targetAddr.hostAddress) break
                } else {
                    sb.append(" $ttl  * * *\n")
                }
            }
            sb.toString()
        } catch (e: Exception) {
            sb.append("路由探测失败: ${e.message?.take(60)}\n").toString()
        }
    }

    // ── 延迟测试（OkHttp HEAD，不用 curl）────────────────────
    private fun measureLatency(target: String): Double {
        return try {
            val start = System.currentTimeMillis()
            val req = Request.Builder()
                .url("https://$target")
                .head()
                .build()
            val resp = httpClient.newCall(req).execute()
            resp.close()
            (System.currentTimeMillis() - start).toDouble()
        } catch (e: Exception) { -1.0 }
    }

    // ── UI 辅助 ───────────────────────────────────────────────
    private fun log(tv: TextView, msg: String) {
        tv.append(msg + "\n")
        // 找外层 ScrollView 滚到底
        var p = tv.parent
        while (p != null) {
            if (p is ScrollView) { p.post { p.fullScroll(View.FOCUS_DOWN) }; break }
            p = (p as? android.view.View)?.parent
        }
    }

    private fun buildIpInfoText(info: JSONObject?): String {
        if (info == null) return "IP 信息获取失败"
        val ip      = info.optString("ip")
        val city    = info.optString("city")
        val country = info.optString("country")
        val org     = info.optString("org")
        val type    = classifyOrg(org)
        return "出口 IP: $ip\n位置: $city, $country\n运营商: ${org.take(40)}\nIP类型: $type"
    }

    private fun classifyOrg(org: String): String {
        val o = org.lowercase()
        val dc  = listOf("amazon","aws","google","microsoft","azure","alibaba","vultr","linode",
                         "digitalocean","arosscloud","cognetcloud","cognet","choopa","quadranet",
                         "psychz","hostwinds","buyvm","datacamp","m247","hetzner","ovh","tencent")
        val res = listOf("comcast","at&t","verizon","spectrum","china telecom","china unicom",
                         "china mobile","residential","broadband")
        return when {
            dc.any  { o.contains(it) } -> "🏢 机房 IP（TikTok 高风险）"
            res.any { o.contains(it) } -> "🏠 住宅 IP（TikTok 友好）"
            else -> "❓ 未知类型"
        }
    }

    private fun updateStats(tvStats: TextView, tvVerdict: TextView, ipInfo: JSONObject?) {
        val avg      = if (latencies.isEmpty()) 0.0 else latencies.average()
        val min      = latencies.minOrNull() ?: 0.0
        val max      = latencies.maxOrNull() ?: 0.0
        val jitter   = if (latencies.size > 1) {
            latencies.zipWithNext { a, b -> Math.abs(a - b) }.average()
        } else 0.0
        val lossRate = if (count > 0) lossCount.toDouble() / count * 100 else 0.0

        tvStats.text = "延迟: ${avg.toInt()}ms  (min:${min.toInt()} max:${max.toInt()})\n" +
                "抖动: ${jitter.toInt()}ms  |  丢包: ${"%.1f".format(lossRate)}% ($lossCount/$count)"

        val org     = ipInfo?.optString("org") ?: ""
        val ipType  = classifyOrg(org)
        val (verdict, color) = when {
            ipType.contains("机房") ->
                "🔴 机房 IP — TikTok 账号权重低" to Color.parseColor("#f87171")
            lossRate > 5 || avg > 500 || jitter > 100 ->
                "🔴 线路质量差 — 直播可能严重卡顿" to Color.parseColor("#f87171")
            lossRate > 1 || avg > 200 || jitter > 50 ->
                "🟡 线路质量一般 — 建议持续监控" to Color.parseColor("#fbbf24")
            ipType.contains("住宅") ->
                "🟢 住宅 IP + 线路良好 — 适合 TikTok 直播" to Color.parseColor("#4ade80")
            else ->
                "🟡 线路良好，IP 类型待确认" to Color.parseColor("#fbbf24")
        }
        tvVerdict.text = verdict
        tvVerdict.setTextColor(color)
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
