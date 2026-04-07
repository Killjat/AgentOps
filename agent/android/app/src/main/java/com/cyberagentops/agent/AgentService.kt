package com.cyberagentops.agent

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager

class AgentService : Service() {

    private var agentWs: AgentWebSocket? = null
    private val CHANNEL_ID = "agent_channel"
    private val NOTIF_ID = 1
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildNotification("准备连接...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification)
        }
        // 持有 WakeLock，息屏后保持 CPU 运行
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CyberAgent::WakeLock")
        wakeLock?.acquire()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 处理用户主动断开
        if (intent?.action == ACTION_DISCONNECT) {
            Log.i("AgentService", "用户主动断开")
            agentWs?.disconnect()
            agentWs = null
            updateNotification("已断开")
            return START_NOT_STICKY
        }

        Log.i("AgentService", "收到启动指令，尝试连接...")
        if (agentWs == null) {
            agentWs = AgentWebSocket(this) { status ->
                updateNotification(status)
                val i = Intent("com.cyberagent.STATUS_UPDATE").putExtra("status", status)
                LocalBroadcastManager.getInstance(this).sendBroadcast(i)
            }
        }
        agentWs?.disconnect()
        agentWs?.connect()
        return START_STICKY  // 系统杀死后自动重启
    }

    override fun onDestroy() {
        agentWs?.disconnect()
        wakeLock?.release()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    fun updateNotification(status: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIF_ID, buildNotification(status))
    }

    private fun buildNotification(status: String): Notification {
        val agentId = AgentConfig.getAgentId(this)
        // 点击通知打开 MainActivity
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        // 断开按钮
        val disconnectIntent = Intent(this, AgentService::class.java).apply { action = ACTION_DISCONNECT }
        val disconnectPi = PendingIntent.getService(this, 1, disconnectIntent, PendingIntent.FLAG_IMMUTABLE)

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("CyberAgent · $agentId")
            .setContentText(status)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setContentIntent(pi)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "断开", disconnectPi)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL_ID) == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID, "Agent 服务",
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = "CyberAgentOps 后台服务"
                    setShowBadge(false)
                    setSound(null, null)
                    enableVibration(false)
                }
                manager.createNotificationChannel(channel)
            }
        }
    }

    companion object {
        const val ACTION_DISCONNECT = "com.cyberagentops.agent.DISCONNECT"
    }
}
