package com.cyberagentops.netcheck

import android.Manifest
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager

class MainActivity : AppCompatActivity() {

    private lateinit var tvAgentId: TextView
    private lateinit var tvStatus: TextView
    private lateinit var statusDot: android.view.View
    private lateinit var etServerUrl: EditText
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val status = intent.getStringExtra("status") ?: return
            // 忽略断开状态，重新打开 APP 时直接显示连接中
            if (status == "已断开") return
            runOnUiThread {
                tvStatus.text = status.uppercase()
                if (status == "已连接") {
                    tvStatus.setTextColor(Color.GREEN)
                    statusDot.setBackgroundColor(Color.GREEN)
                } else {
                    tvStatus.setTextColor(Color.parseColor("#7C6AF7"))
                    statusDot.setBackgroundColor(Color.parseColor("#7C6AF7"))
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvAgentId = findViewById(R.id.tvAgentId)
        tvStatus = findViewById(R.id.tvStatus)
        statusDot = findViewById(R.id.statusDot)
        etServerUrl = findViewById(R.id.etServerUrl)
        btnStart = findViewById(R.id.btnStart)
        btnStop = findViewById(R.id.btnStop)

        tvAgentId.text = AgentConfig.getAgentId(this)
        etServerUrl.setText(AgentConfig.getServerUrl(this))

        btnStart.setOnClickListener {
            val url = etServerUrl.text.toString().trim()
            if (url.isEmpty()) {
                Toast.makeText(this, "请输入 Server URL", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            AgentConfig.save(this, url)
            if (checkAndRequestPermissions()) {
                startAgentService()
            }
        }

        btnStop.setOnClickListener {
            // 发送主动断开指令，Service 收到后停止重连
            val intent = Intent(this, AgentService::class.java).apply {
                action = AgentService.ACTION_DISCONNECT
            }
            startService(intent)
            setStatus(false)
        }

        // 常亮模式开关
        val switchKeepScreen = findViewById<Switch>(R.id.switchKeepScreen)
        switchKeepScreen.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) {
                window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                Toast.makeText(this, "屏幕常亮已开启", Toast.LENGTH_SHORT).show()
            } else {
                window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }
        
        LocalBroadcastManager.getInstance(this)
            .registerReceiver(statusReceiver, IntentFilter("com.cyberagent.STATUS_UPDATE"))

        // 网络检测入口
        findViewById<android.widget.Button>(R.id.btnNetCheck).setOnClickListener {
            startActivity(android.content.Intent(this, NetCheckActivity::class.java))
        }

        // 首次启动引导：请求忽略电池优化
        checkBatteryOptimization()
    }

    override fun onResume() {
        super.onResume()
        tvStatus.text = "CONNECTING..."
        tvStatus.setTextColor(Color.parseColor("#7C6AF7"))
        statusDot.setBackgroundColor(Color.parseColor("#7C6AF7"))
        val url = AgentConfig.getServerUrl(this)
        if (url.isNotEmpty()) {
            startAgentService()
        }
    }

    override fun onPause() {
        super.onPause()
        // APP 退到后台，Service 继续运行，不停止
    }

    override fun onDestroy() {
        LocalBroadcastManager.getInstance(this).unregisterReceiver(statusReceiver)
        super.onDestroy()
    }

    private fun checkAndRequestPermissions(): Boolean {
        val permissions = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) 
                != PackageManager.PERMISSION_GRANTED) {
                permissions.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (permissions.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, permissions.toTypedArray(), 1001)
            return false
        }
        return true
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001 && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startAgentService()
        }
    }

    private fun startAgentService() {
        try {
            val intent = Intent(this, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            tvStatus.text = "CONNECTING..."
            tvStatus.setTextColor(Color.parseColor("#7C6AF7"))
            statusDot.setBackgroundColor(Color.parseColor("#7C6AF7"))
        } catch (e: Exception) {
            Toast.makeText(this, "启动失败: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun setStatus(online: Boolean) {
        if (!online) {
            tvStatus.text = "OFFLINE"
            tvStatus.setTextColor(Color.parseColor("#475569"))
            statusDot.setBackgroundColor(Color.parseColor("#475569"))
        }
    }

    private fun checkBatteryOptimization() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        val prefs = getSharedPreferences("cyberagent", Context.MODE_PRIVATE)
        val shown = prefs.getBoolean("battery_guide_shown", false)
        if (shown && pm.isIgnoringBatteryOptimizations(packageName)) return

        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            AlertDialog.Builder(this)
                .setTitle("⚡ 需要后台运行权限")
                .setMessage("为确保息屏后保持连接，请允许 CyberAgent 忽略电池优化。\n\n点击「去设置」→ 找到 CyberAgent → 选择「不受限制」")
                .setPositiveButton("去设置") { _, _ ->
                    prefs.edit().putBoolean("battery_guide_shown", true).apply()
                    try {
                        // 直接请求忽略电池优化
                        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                            data = Uri.parse("package:$packageName")
                        }
                        startActivity(intent)
                    } catch (e: Exception) {
                        // 部分厂商不支持，跳转到通用电池设置
                        startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                    }
                }
                .setNegativeButton("稍后") { _, _ ->
                    prefs.edit().putBoolean("battery_guide_shown", true).apply()
                }
                .setCancelable(false)
                .show()
        }
    }
}
