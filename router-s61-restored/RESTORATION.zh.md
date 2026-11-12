# Router S61 原接入恢復與現場缺口（2026-11-12）

本輪依使用者「開始」接續原 Router。未建立第二套 Router、DSH 服務、Source、任務庫或授權。

## 已完成
- 原 S61 ZIP 先前下載403阻塞已排除；本輪下載2701228 bytes，
  SHA256 1500784f3eb88d2d73cd2a3511720054fcbd1553afbf1268008f6beec5fe059c。
- HANDOFF-MANIFEST 的553項檔案逐一大小／SHA256全相符；ZIP CRC通過。
- 原 native bridge 增量23217 bytes，
  SHA256 2d306cde0cc6ebd7319a9065003c34a2c0ae22161a1c436726d8c0e36c398ac5。
- 精確恢復原 observed_start_view、cold_idle、durable_report、native_report、
  native_turn_idle 及 formal_driver_entry；S61授權文字保留，PINS保存。
- Node24.22.1真匯入三個原 exports 成功，未呼叫 Source／driver.run／派送。
- 新只讀稽核入口 ops/router_s61_readonly_audit.py：只讀AST／檔案雜湊，
  不匯入Gateway App、不初始化服務、不碰SQL／Source／網路／派工。
- 本Work當次817個工具中，未找到原 native read/send/interrupt callable：
  TOOL_NOT_FOUND。模型 collaboration 不是Gateway可呼用的程式API。

## 原Codespace本輪觀察（終端畫面讀驗，非已下載原始log）
- 原四檔 pins 已在宿主 /tmp/router-s61-live-audit.json 保存；
  本備份未下載該原始JSON，不以手抄值冒原件。
- app末尾已有 create_app(run_bindings=deployment_run_bindings(),
  source_runtime=default_platform_source_runtime(),redis_events=...)。
  與11/11舊現場不同：啟動呼叫已補，但未帶可信host／enable。
- dsh executable未找到。於 /workspaces、原Gateway恢復根及/tmp 的
  cordis.patch.yml 定向搜尋沒有命中。這不證整台機器或其他宿主都沒有Router。
- 原Source HTTP備份本輪讀到v9。PlatformSourceStore明示checkpoint-only，
  不授予 native Work、Caller、派工、可信完成或本人ACK；不能將其自動升級。
- source_holder_ports 現用工廠已具備同run/owner/thread與Source revision檢查；
  缺同一當代 S61 ticket/assignment/start、原 Store／Passes、可信completion及原native bridge。

## 結論與唯一續辦方向
RESTORED_AND_STAGED，尚未正式上線，G0 UNKNOWN，Gate1 DENY。
不能用Codex子任務成功或新增callback字串代替原Work native API。
完整S61 Runtime/Registry/Fiber啟動與五類正式零派送整合 NOT_RUN；
沒有新任務、授權、回執或person ACK。
已有介面本輪無須再造；需在實際持有原生 read/send/interrupt 的受權runtime，
將原正式driver掛到同Source/Holder，再用同S61上下文裝配。
尚未取得該能力時維持缺件，不重播歷史UNKNOWN，不重試403繞路。
本輪不重啟Gateway或原Deerworker，不消耗其已用完的受控重啟預算。

本資料夾僅恢復原無外部依賴的接入模組與稽核入口，不冒充完整DSH安裝；
完整原S61 ZIP及553項原manifest另納入本輪備份。


## 原宿主實際續辦結果
- 以固定提交203bdddf608fcb9f30583db174d565db0610f0e8建立獨立worktree
  /tmp/deer-router-s61-staging，未改原服務checkout。
- 稽核腳本實際讀現用Gateway，回 startup_run_bindings=[deployment_run_bindings()]，
  deployment_default_enabled=false、static_default_off=true、runtime_slot_verified=false。
- 原宿主Node實際匯入 cold_idle.mjs 與 formal_driver_entry.mjs 成功，
  只載入模組，無Source讀取、runtime啟動或派送。
- 宿主原回執已保存到 evidence/router-s61-onhost-audit-20261112.json，
  本地detached commit 3daf6e1；git push原分支HTTP403，未推送成功。
  沒有刷新／擴充Git憑證或重試該拒絕。原回執仍在宿主，
  本輪備份僅包含終端觀察摘要，不能稱原始JSON已取回。
- 先前203bdddf版說明中run_bindings的手抄SHA有誤，該值不作pin；
  以宿主機器生成JSON為準。本版移除手抄四檔SHA。
