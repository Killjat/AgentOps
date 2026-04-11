package com.cyberagentops.agent

import android.content.Context
import android.content.SharedPreferences

object AgentConfig {
    private const val PREFS_NAME = "agent_config"
    private const val KEY_SERVER_URL = "server_url"
    private const val KEY_AGENT_ID = "agent_id"

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getServerUrl(context: Context): String {
        val saved = prefs(context).getString(KEY_SERVER_URL, "") ?: ""
        return if (saved.isEmpty()) "https://47.111.28.162:8443" else saved
    }

    fun getAgentId(context: Context): String {
        val prefs = prefs(context)
        var id = prefs.getString(KEY_AGENT_ID, "") ?: ""
        if (id.isEmpty()) {
            // 用硬件信息生成稳定 ID，不依赖 ANDROID_ID（ANDROID_ID 随签名变化）
            val raw = "${android.os.Build.MODEL}-${android.os.Build.HARDWARE}-${android.os.Build.BOARD}"
            val hash = java.security.MessageDigest.getInstance("MD5")
                .digest(raw.toByteArray())
                .joinToString("") { "%02x".format(it) }
                .take(8)
            id = "android-$hash"
            prefs.edit().putString(KEY_AGENT_ID, id).apply()
        }
        return id
    }

    fun save(context: Context, serverUrl: String) {
        prefs(context).edit().putString(KEY_SERVER_URL, serverUrl).apply()
    }
}
