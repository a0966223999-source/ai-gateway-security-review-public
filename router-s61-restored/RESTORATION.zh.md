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
- 原 Gateway run_bindings.py SHA256 eca3d6c6fa0a824a7374c4534218e3c9bd190b42c26affda1f56f5337d91d9ae。
- source_holder_ports.py SHA256 93df2c1ee7a3023a1ea78d8d199f9ddd8f4512020c4e17dc0eee88572cf26cdf。
- deployment_event_ports.py SHA256 a7deb5a2995398c5263263c6ee3a63647480ea0489aac3225ce34f577c3fb7f。
- platform_source_host.py SHA256 4a24323dbb9cffcd62a19a9dc0509e4f30eb5d858e7d68bb38a781b8152337b3。
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

