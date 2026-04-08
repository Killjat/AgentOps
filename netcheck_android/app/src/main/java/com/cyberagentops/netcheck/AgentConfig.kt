package com.cyberagentops.netcheck

import android.content.Context
import android.content.SharedPreferences

object AgentConfig {
    private const val PREFS_NAME = "agent_config"
    private const val KEY_SERVER_URL = "server_url"
    private const val KEY_AGENT_ID = "agent_id"

    // 服务器地址内置，用户无需配置
    private const val DEFAULT_SERVER_URL = "https://47.111.28.162:8443"

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getServerUrl(context: Context): String {
        val saved = prefs(context).getString(KEY_SERVER_URL, "") ?: ""
        return if (saved.isEmpty()) DEFAULT_SERVER_URL else saved
    }

    fun getAgentId(context: Context): String {
        val prefs = prefs(context)
        var id = prefs.getString(KEY_AGENT_ID, "") ?: ""
        if (id.isEmpty()) {
            id = "android-" + android.provider.Settings.Secure.getString(
                context.contentResolver,
                android.provider.Settings.Secure.ANDROID_ID
            ).take(8)
            prefs.edit().putString(KEY_AGENT_ID, id).apply()
        }
        return id
    }

    fun save(context: Context, serverUrl: String) {
        prefs(context).edit().putString(KEY_SERVER_URL, serverUrl).apply()
    }
}
