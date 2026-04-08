package com.cyberagentops.netcheck

import android.graphics.Color
import android.os.Bundle
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class NetCheckActivity : AppCompatActivity() {

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var running = false
    private var stopFlag = false

    // 统计
    private var count = 0
    private var lossCount = 0
    private val latencies = mutableListOf<Double>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_netcheck)

        val btnStart = findViewById<Button>(R.id.btnStartCheck)
        val btnStop = findViewById<Button>(R.id.btnStopCheck)
        val etTarget = findViewById<EditText>(R.id.etTarget)
        val tvVerdict = findViewById<TextView>(R.id.tvVerdict)
        val tvStats = findViewById<TextView>(R.id.tvStats)
        val tvLog = findViewById<TextView>(R.id.tvLog)
        val tvIpInfo = findViewById<TextView>(R.id.tvIpInfo)

        btnStart.setOnClickListener {
            val target = etTarget.text.toString().trim().ifEmpty { "tiktok.com" }
            startCheck(target, tvVerdict, tvStats, tvLog, tvIpInfo, btnStart, btnStop)
        }

        btnStop.setOnClickListener {
            stopFlag = true
            running = false
            btnStop.visibility = android.view.View.GONE
            btnStart.isEnabled = true
        }
    }

    private fun startCheck(
        target: String,
        tvVerdict: TextView, tvStats: TextView,
        tvLog: TextView, tvIpInfo: TextView,
        btnStart: Button, btnStop: Button
    ) {
        if (running) return
        running = true
        stopFlag = false
        count = 0; lossCount = 0; latencies.clear()
        btnStart.isEnabled = false
        btnStop.visibility = android.view.View.VISIBLE
        tvLog.text = ""

        scope.launch {
            // 1. 获取出口 IP 信息
            val ipInfo = withContext(Dispatchers.IO) { fetchIpInfo() }
            tvIpInfo.text = buildIpInfoText(ipInfo)

            // 2. traceroute
            tvLog.append("🔍 正在追踪路由...\n")
            val trResult = withContext(Dispatchers.IO) { runTraceroute(target) }
            tvLog.append(trResult + "\n")

            // 3. DNS 泄露检测
            tvLog.append("🔎 DNS 泄露检测...\n")
            val dnsResult = withContext(Dispatchers.IO) { checkDnsLeak(target) }
            tvLog.append(dnsResult + "\n\n")

            // 4. 持续 ping 测延迟
            tvLog.append("📡 开始延迟测试...\n")
            repeat(20) { i ->
                if (stopFlag) return@repeat
                val ms = withContext(Dispatchers.IO) { measureLatency(target) }
                count++
                if (ms < 0) {
                    lossCount++
                    tvLog.append("[$i] ❌ 超时\n")
                } else {
                    latencies.add(ms)
                    tvLog.append("[$i] ${ms.toInt()}ms\n")
                }
                updateStats(tvStats, tvVerdict, ipInfo)
                delay(2000)
            }

            running = false
            btnStart.isEnabled = true
            btnStop.visibility = android.view.View.GONE
        }
    }

    private fun fetchIpInfo(): JSONObject? {
        return try {
            val conn = URL("https://ipinfo.io/json").openConnection() as HttpURLConnection
            conn.connectTimeout = 8000; conn.readTimeout = 8000
            val text = conn.inputStream.bufferedReader().readText()
            JSONObject(text)
        } catch (e: Exception) { null }
    }

    private fun buildIpInfoText(info: JSONObject?): String {
        if (info == null) return "IP 信息获取失败"
        val ip = info.optString("ip")
        val city = info.optString("city")
        val country = info.optString("country")
        val org = info.optString("org")
        val ipType = classifyOrg(org)
        return "出口 IP: $ip\n位置: $city, $country\n运营商: $org\nIP类型: $ipType"
    }

    private fun classifyOrg(org: String): String {
        val o = org.lowercase()
        val dcKeywords = listOf("amazon","aws","google","microsoft","azure","alibaba","vultr","linode","digitalocean","arosscloud","cognetcloud","cognet","choopa","quadranet","psychz","hostwinds","buyvm","datacamp","m247","serverius","hetzner","ovh")
        val resKeywords = listOf("comcast","at&t","verizon","spectrum","china telecom","china unicom","china mobile","residential")
        return when {
            dcKeywords.any { o.contains(it) } -> "🏢 机房 IP（TikTok 高风险）"
            resKeywords.any { o.contains(it) } -> "🏠 住宅 IP（TikTok 友好）"
            else -> "❓ 未知类型"
        }
    }

    private fun runTraceroute(target: String): String {
        // Android 没有 traceroute，用多跳 ping 模拟路径
        val sb = StringBuilder()
        sb.append("路由追踪（TTL 递增 ping 模拟）:\n")
        for (ttl in listOf(1, 2, 3, 5, 8, 10, 15)) {
            val result = CommandExecutor.exec(
                "ping -c 1 -t $ttl -W 2 $target 2>&1 | grep -E 'From|time='", 5
            )
            if (result.output.isNotBlank()) {
                sb.append("TTL=$ttl: ${result.output.trim().take(80)}\n")
            }
        }
        // 最终目标延迟
        val final = CommandExecutor.exec("ping -c 2 -W 3 $target 2>&1 | tail -3", 10)
        sb.append("\n目标: $final.output.trim().take(200)")
        return sb.toString()
    }

    private fun checkDnsLeak(target: String): String {
        return try {
            // 本机 DNS 解析
            val localIps = java.net.InetAddress.getAllByName(target).map { it.hostAddress }.take(3)

            // Google DoH 查询（绕过本机 DNS）
            val conn = URL("https://dns.google/resolve?name=$target&type=A").openConnection() as HttpURLConnection
            conn.connectTimeout = 6000; conn.readTimeout = 6000
            conn.setRequestProperty("Accept", "application/dns-json")
            val dohText = conn.inputStream.bufferedReader().readText()
            val dohJson = JSONObject(dohText)
            val dohIps = mutableListOf<String>()
            val answers = dohJson.optJSONArray("Answer")
            if (answers != null) {
                for (i in 0 until answers.length()) {
                    val a = answers.getJSONObject(i)
                    if (a.optInt("type") == 1) dohIps.add(a.optString("data"))
                }
            }

            val leaked = localIps.isNotEmpty() && dohIps.isNotEmpty() &&
                    localIps.none { it in dohIps }

            buildString {
                append("本机 DNS: ${localIps.joinToString(", ")}\n")
                append("Google DNS: ${dohIps.take(3).joinToString(", ")}\n")
                if (leaked) {
                    append("⚠️ DNS 泄露！本机 DNS 与 Google DNS 结果不一致\n")
                    append("代理可能未接管 DNS，TikTok 可识别真实位置")
                } else {
                    append("✅ DNS 正常，无泄露")
                }
            }
        } catch (e: Exception) {
            "DNS 检测失败: ${e.message?.take(60)}"
        }
    }

    private fun measureLatency(target: String): Double {
        return try {
            val start = System.currentTimeMillis()
            val conn = URL("https://$target").openConnection() as HttpURLConnection
            conn.connectTimeout = 5000; conn.readTimeout = 5000
            conn.requestMethod = "HEAD"
            conn.connect()
            val ms = (System.currentTimeMillis() - start).toDouble()
            conn.disconnect()
            ms
        } catch (e: Exception) { -1.0 }
    }

    private fun updateStats(tvStats: TextView, tvVerdict: TextView, ipInfo: JSONObject?) {
        val avg = if (latencies.isEmpty()) 0.0 else latencies.average()
        val min = latencies.minOrNull() ?: 0.0
        val max = latencies.maxOrNull() ?: 0.0
        val jitter = if (latencies.size > 1) {
            val diffs = latencies.zipWithNext { a, b -> Math.abs(a - b) }
            diffs.average()
        } else 0.0
        val lossRate = if (count > 0) lossCount.toDouble() / count * 100 else 0.0

        tvStats.text = "延迟: ${avg.toInt()}ms (min:${min.toInt()} max:${max.toInt()})\n" +
                "抖动: ${jitter.toInt()}ms | 丢包: ${"%.1f".format(lossRate)}% ($lossCount/$count)"

        // 判决
        val org = ipInfo?.optString("org") ?: ""
        val ipType = classifyOrg(org)
        val verdict: String
        val color: Int
        when {
            ipType.contains("机房") -> {
                verdict = "🔴 机房 IP — TikTok 账号权重低，不建议使用"
                color = Color.parseColor("#f87171")
            }
            lossRate > 5 || avg > 500 || jitter > 100 -> {
                verdict = "🔴 线路质量差 — 直播可能严重卡顿"
                color = Color.parseColor("#f87171")
            }
            lossRate > 1 || avg > 200 || jitter > 50 -> {
                verdict = "🟡 线路质量一般 — 建议持续监控"
                color = Color.parseColor("#fbbf24")
            }
            ipType.contains("住宅") -> {
                verdict = "🟢 住宅 IP + 线路良好 — 适合 TikTok 直播"
                color = Color.parseColor("#4ade80")
            }
            else -> {
                verdict = "🟡 线路良好，IP 类型待确认"
                color = Color.parseColor("#fbbf24")
            }
        }
        tvVerdict.text = verdict
        tvVerdict.setTextColor(color)
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
