package com.cyberagentops.netcheck

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val serverUrl = AgentConfig.getServerUrl(context)
            if (serverUrl.isNotEmpty()) {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, AgentService::class.java)
                )
            }
        }
    }
}
