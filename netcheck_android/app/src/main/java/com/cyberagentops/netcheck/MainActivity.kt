package com.cyberagentops.netcheck

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 启动后台服务
        AgentConfig.save(this, AgentConfig.getServerUrl(this))
        try {
            val serviceIntent = Intent(this, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
        } catch (e: Exception) {
            // ignore
        }

        // 跳转到主界面
        startActivity(Intent(this, NetCheckActivity::class.java))
        finish()
    }
}
