with open('web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = '        <!-- 顶部 Summary'
end_marker = '\n      <!-- ── 目标机器 ── -->'
start_idx = content.find(start_marker)
end_idx = content.find(end_marker)
print(f"start:{start_idx}, end:{end_idx}")

new_section = '''        <!-- 顶部 4 大指标 -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:32px">
          <div style="background:linear-gradient(135deg,#1a1d27,#1e2240);border:1px solid #2d3148;border-radius:16px;padding:20px 24px;position:relative;overflow:hidden">
            <div style="position:absolute;right:16px;top:16px;font-size:36px;opacity:.06">🖥</div>
            <div style="font-size:10px;color:#475569;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">TOTAL NODES</div>
            <div style="font-size:44px;font-weight:800;color:#e2e8f0;line-height:1">{{ agents.length }}</div>
            <div style="font-size:11px;color:#334155;margin-top:8px">全部节点</div>
          </div>
          <div style="background:linear-gradient(135deg,#0a1f10,#0d2a18);border:1px solid #1a4a2e;border-radius:16px;padding:20px 24px;position:relative;overflow:hidden">
            <div style="position:absolute;right:16px;top:16px;font-size:36px;opacity:.12">✅</div>
            <div style="font-size:10px;color:#166534;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">ONLINE</div>
            <div style="font-size:44px;font-weight:800;color:#4ade80;line-height:1">{{ agents.filter(a=>a.status==="online").length }}</div>
            <div style="font-size:11px;color:#166534;margin-top:8px">在线节点</div>
          </div>
          <div style="background:linear-gradient(135deg,#1f0a0a,#2a0d0d);border:1px solid #4a1a1a;border-radius:16px;padding:20px 24px;position:relative;overflow:hidden">
            <div style="position:absolute;right:16px;top:16px;font-size:36px;opacity:.12">⚠️</div>
            <div style="font-size:10px;color:#7f1d1d;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">OFFLINE</div>
            <div style="font-size:44px;font-weight:800;color:#f87171;line-height:1">{{ agents.filter(a=>a.status!=="online").length }}</div>
            <div style="font-size:11px;color:#7f1d1d;margin-top:8px">离线节点</div>
          </div>
          <div style="background:linear-gradient(135deg,#16102a,#1e1540);border:1px solid #3d2d6e;border-radius:16px;padding:20px 24px;position:relative;overflow:hidden">
            <div style="position:absolute;right:16px;top:16px;font-size:36px;opacity:.12">⚡</div>
            <div style="font-size:10px;color:#5b21b6;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">AVG CPU</div>
            <div style="font-size:44px;font-weight:800;color:#a78bfa;line-height:1">
              {{ agents.filter(a=>a.metrics&&a.metrics.cpu_usage>=0).length ? (agents.filter(a=>a.metrics&&a.metrics.cpu_usage>=0).reduce((s,a)=>s+a.metrics.cpu_usage,0)/agents.filter(a=>a.metrics&&a.metrics.cpu_usage>=0).length).toFixed(1) : "—" }}<span style="font-size:22px">%</span>
            </div>
            <div style="font-size:11px;color:#5b21b6;margin-top:8px">平均 CPU 使用率</div>
          </div>
        </div>

        <!-- 筛选 Tab -->
        <div style="display:flex;gap:4px;margin-bottom:24px;background:#0a0d14;border-radius:10px;padding:4px;width:fit-content">
          <button @click="monitorFilter=\'all\'"
            :style="monitorFilter===\'all\'?\'background:#1a1d27;color:#e2e8f0;box-shadow:0 2px 8px #0006\':\'color:#334155\'"
            style="border:none;border-radius:7px;padding:8px 22px;font-size:13px;cursor:pointer;transition:all .2s;font-weight:600;letter-spacing:.3px">
            ALL &nbsp;<span style="font-size:11px;opacity:.5">{{ agents.length }}</span>
          </button>
          <button @click="monitorFilter=\'online\'"
            :style="monitorFilter===\'online\'?\'background:#0a1f10;color:#4ade80;box-shadow:0 2px 8px #0006\':\'color:#334155\'"
            style="border:none;border-radius:7px;padding:8px 22px;font-size:13px;cursor:pointer;transition:all .2s;font-weight:600;letter-spacing:.3px">
            ONLINE &nbsp;<span style="font-size:11px;opacity:.5">{{ agents.filter(a=>a.status==="online").length }}</span>
          </button>
          <button @click="monitorFilter=\'offline\'"
            :style="monitorFilter===\'offline\'?\'background:#1f0a0a;color:#f87171;box-shadow:0 2px 8px #0006\':\'color:#334155\'"
            style="border:none;border-radius:7px;padding:8px 22px;font-size:13px;cursor:pointer;transition:all .2s;font-weight:600;letter-spacing:.3px">
            OFFLINE &nbsp;<span style="font-size:11px;opacity:.5">{{ agents.filter(a=>a.status!=="online").length }}</span>
          </button>
        </div>

        <div v-if="!agents.length" style="text-align:center;padding:80px;color:#1e2240;font-size:16px;letter-spacing:2px">NO AGENTS DEPLOYED</div>

        <!-- 节点卡片 -->
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px">
          <div v-for="a in agents" :key="a.agent_id"
               v-show="monitorFilter===\'all\' || (monitorFilter===\'online\'&&a.status===\'online\') || (monitorFilter===\'offline\'&&a.status!==\'online\')"
               :style="a.status===\'online\'
                 ? \'background:#0d1117;border:1px solid #1a2a1a;border-radius:16px;overflow:hidden;transition:all .3s\'
                 : \'background:#0d1117;border:1px solid #2a1212;border-radius:16px;overflow:hidden;opacity:.8;transition:all .3s\'">

            <!-- 卡片头 -->
            <div style="padding:16px 20px;display:flex;justify-content:space-between;align-items:center"
                 :style="a.status===\'online\'?\'border-bottom:1px solid #1a2a1a\':\'border-bottom:1px solid #2a1212\'">
              <div style="display:flex;align-items:center;gap:12px">
                <div style="width:42px;height:42px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0"
                     :style="a.status===\'online\'?\'background:#0a1f0a\':\'background:#1f0a0a\'">
                  {{ a.os_type==="windows"?"🪟": a.os_type==="macos"?"🍎": a.os_type==="android"?"🤖": "🐧" }}
                </div>
                <div>
                  <div style="font-weight:700;font-size:15px;color:#e2e8f0;letter-spacing:.3px">{{ a.name || a.agent_id }}</div>
                  <div style="font-size:11px;color:#2d3a4a;margin-top:3px;font-family:monospace">
                    {{ (a.metrics&&a.metrics.network&&(a.metrics.network.public||a.metrics.network.eth)) || a.agent_id }}
                  </div>
                </div>
              </div>
              <div style="text-align:right">
                <div style="display:flex;align-items:center;gap:7px;justify-content:flex-end">
                  <span :style="{width:\'9px\',height:\'9px\',borderRadius:\'50%\',display:\'inline-block\',
                    background: a.status===\'online\'?\'#4ade80\':\'#ef4444\',
                    boxShadow: a.status===\'online\'?\'0 0 10px #4ade80\':\'none\',
                    animation: a.status===\'online\'?\'pulse 2s infinite\':\'none\'}"></span>
                  <span style="font-size:11px;font-weight:700;letter-spacing:1px"
                        :style="{color: a.status===\'online\'?\'#4ade80\':\'#ef4444\'}">
                    {{ a.status==="online"?"ONLINE":"OFFLINE" }}
                  </span>
                </div>
                <div style="font-size:10px;color:#1e2a2a;margin-top:4px;letter-spacing:.5px">{{ (a.os_type||"").toUpperCase() }}</div>
              </div>
            </div>

            <!-- 在线节点 -->
            <div v-if="a.status===\'online\'" style="padding:16px 20px">
              <div v-if="a.metrics">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;padding:9px 14px;background:#0a1a0a;border-radius:8px;border:1px solid #1a3a1a">
                  <span style="font-size:10px;color:#166534;letter-spacing:1px;text-transform:uppercase">Last Report</span>
                  <span style="font-size:12px;color:#4ade80;font-weight:600;font-family:monospace">
                    {{ a.metrics.timestamp ? new Date(a.metrics.timestamp).toLocaleString("zh-CN") : "—" }}
                  </span>
                </div>
                <div style="margin-bottom:16px">
                  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px">
                    <span style="font-size:10px;color:#334155;letter-spacing:1.5px;text-transform:uppercase">CPU Usage</span>
                    <span style="font-size:22px;font-weight:800;line-height:1"
                          :style="{color: a.metrics.cpu_usage>80?\'#f87171\':a.metrics.cpu_usage>60?\'#fbbf24\':\'#4ade80\'}">
                      {{ a.metrics.cpu_usage >= 0 ? a.metrics.cpu_usage : "N/A" }}<span style="font-size:13px">%</span>
                    </span>
                  </div>
                  <div style="background:#0a0d14;border-radius:6px;height:6px;overflow:hidden">
                    <div :style="{width: Math.max(0,Math.min(100,a.metrics.cpu_usage||0))+\'%\',height:\'100%\',borderRadius:\'6px\',transition:\'width 1s ease\',
                      background: a.metrics.cpu_usage>80?\'linear-gradient(90deg,#dc2626,#f87171)\':a.metrics.cpu_usage>60?\'linear-gradient(90deg,#d97706,#fbbf24)\':\'linear-gradient(90deg,#15803d,#4ade80)\'}"></div>
                  </div>
                </div>
                <div style="margin-bottom:16px">
                  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                    <span style="font-size:10px;color:#334155;letter-spacing:1.5px;text-transform:uppercase">Memory</span>
                    <span style="font-size:18px;font-weight:700;color:#a78bfa">
                      {{ a.metrics.hardware&&a.metrics.hardware.memory_mb ? (a.metrics.hardware.memory_mb/1024).toFixed(1)+" GB" : "N/A" }}
                    </span>
                  </div>
                </div>
                <div v-if="a.metrics.disk&&a.metrics.disk.length" style="margin-bottom:14px">
                  <div style="font-size:10px;color:#334155;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px">Disk</div>
                  <div v-for="d in a.metrics.disk.slice(0,3)" :key="d.mount" style="margin-bottom:10px">
                    <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:5px">
                      <span style="color:#2d3a4a;font-family:monospace">{{ d.mount }}</span>
                      <span :style="{color: parseInt(d.use_pct)>85?\'#f87171\':parseInt(d.use_pct)>70?\'#fbbf24\':\'#334155\',fontWeight:600}">
                        {{ d.used }} / {{ d.size }} &nbsp;{{ d.use_pct }}
                      </span>
                    </div>
                    <div style="background:#0a0d14;border-radius:4px;height:4px;overflow:hidden">
                      <div :style="{width: d.use_pct,height:\'100%\',borderRadius:\'4px\',
                        background: parseInt(d.use_pct)>85?\'linear-gradient(90deg,#dc2626,#f87171)\':parseInt(d.use_pct)>70?\'linear-gradient(90deg,#d97706,#fbbf24)\':\'linear-gradient(90deg,#1d4ed8,#60a5fa)\'}"></div>
                    </div>
                  </div>
                </div>
                <div v-if="a.metrics.network_io&&Object.keys(a.metrics.network_io).length" style="padding-top:12px;border-top:1px solid #1a2a1a">
                  <div style="font-size:10px;color:#334155;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px">Network I/O</div>
                  <div v-for="(io, iface) in a.metrics.network_io" :key="iface" style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
                    <span style="color:#1e2a2a;font-family:monospace">{{ iface }}</span>
                    <span><span style="color:#4ade80">↓ {{ io.rx_kbps }}</span><span style="color:#1e2a2a"> / </span><span style="color:#60a5fa">↑ {{ io.tx_kbps }} KB/s</span></span>
                  </div>
                </div>
              </div>
              <div v-else style="padding:20px 0;text-align:center;color:#1e2a2a;font-size:12px;letter-spacing:1px">WAITING FOR DATA...</div>
            </div>

            <!-- 离线节点 -->
            <div v-else style="padding:20px">
              <div style="padding:16px 18px;background:#1a0808;border-radius:10px;border:1px solid #3d1212;margin-bottom:14px">
                <div style="font-size:10px;color:#7f1d1d;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px">OFFLINE SINCE</div>
                <div style="font-size:16px;color:#f87171;font-weight:700;font-family:monospace">
                  {{ a.last_seen ? new Date(a.last_seen).toLocaleString("zh-CN") : "Unknown" }}
                </div>
                <div style="font-size:12px;color:#7f1d1d;margin-top:6px;font-weight:600" v-if="a.last_seen">
                  {{ Math.round((Date.now()-new Date(a.last_seen))/60000) }} 分钟前离线
                </div>
              </div>
              <div v-if="a.metrics" style="font-size:11px;color:#2d1a1a;line-height:1.8">
                <div>最后 CPU: <span style="color:#4a2020">{{ a.metrics.cpu_usage }}%</span></div>
                <div>最后 IP: <span style="color:#4a2020;font-family:monospace">{{ (a.metrics.network&&(a.metrics.network.public||a.metrics.network.eth)) || "—" }}</span></div>
              </div>
            </div>
          </div>
        </div>
      </div>'''

content = content[:start_idx] + new_section + content[end_idx:]
with open('web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("done, new len:", len(new_section))
