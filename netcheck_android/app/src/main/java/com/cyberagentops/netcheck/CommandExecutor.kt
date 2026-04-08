package com.cyberagentops.netcheck

import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

data class ExecResult(val success: Boolean, val output: String, val error: String)

object CommandExecutor {

    private val DANGEROUS = listOf(
        "rm -rf /", "dd if=/dev/zero", "mkfs.", "format c:"
    )

    fun exec(command: String, timeoutSec: Int = 60): ExecResult {
        // 安全检查
        for (pattern in DANGEROUS) {
            if (command.contains(pattern)) {
                return ExecResult(false, "", "危险命令被拦截: $pattern")
            }
        }

        return try {
            val process = ProcessBuilder("sh", "-c", command)
                .redirectErrorStream(true)
                .start()

            val finished = process.waitFor(timeoutSec.toLong(), TimeUnit.SECONDS)
            if (!finished) {
                process.destroyForcibly()
                return ExecResult(false, "", "命令超时（${timeoutSec}s）")
            }

            val output = BufferedReader(InputStreamReader(process.inputStream))
                .readText()
                .take(10000) // 限制输出大小

            ExecResult(process.exitValue() == 0, output, "")
        } catch (e: Exception) {
            ExecResult(false, "", e.message ?: "执行失败")
        }
    }
}
