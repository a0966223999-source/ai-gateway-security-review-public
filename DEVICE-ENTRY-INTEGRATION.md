# 手機／電腦入口適配

此模組沿用原 `functions_producer_v2.mjs` 及原 `gateway_source_rpc_request`，
沒有新增網路服務。手機／電腦是使用介面，cloud-task／native-chat 是正式資源地址。
兩者不可互相推斷。這是已測試的程式介面，不是已驗證的自動設備偵測器。

## 持權宿主安裝

宿主從自己實際取得的官方建立回件或 chat reference 建立 `hostReferences`。
每筆含宿主自行分配的 opaque `handle`、正式 `referenceKind`、原 `officialReference`。
雲端任務只使用官方 threadId；原生聊天另帶官方 environmentId。
不得使用 runtime environment ID、設備標籤、轉述文字或先前失敗來建立引用。
`deniedThreadIds` 同樣由宿主提供，至少包含仍未解除的已拒絕目標；不可接 RPC 輸入。

```js
import {createDeviceRegistrationEntries} from './functions_device_entry_v1.mjs';

// 三者都來自既有持權宿主的真實上下文，而非遠端 caller 的 JSON。
const entries = createDeviceRegistrationEntries({
  tools,
  hostReferences: officialReferencesAlreadyObtainedByThisHost,
  deniedThreadIds: currentUnresolvedAccessDenials,
});

// 依所選介面呼叫一個入口。設備選擇不構成身分／權限證明。
const prepared = entries.prepareMobile({referenceHandle, requestJson});
// 電腦介面：entries.prepareDesktop({referenceHandle, requestJson});
if (prepared.status !== 'INPUTS_PREPARED_NOT_AUTHORIZED') return prepared;

// 僅在使用者已批准本次原始操作、原正式入口契約皆適用時才送出。
const outcome = await entries.submit(prepared);
```

`prepareMobile` 與 `prepareDesktop` 不呼叫工具。它們只選取宿主已保留的引用，
原樣保留正式 cloud/native 地址。可另傳 selfReport 作 threadId 一致性檢查，
但 selfReport 不決定環境／路由，也不與 locator 同時傳入 RPC。
結果永遠不聲稱已驗證原始 USER、身分、授權、可存取性或 Source。

## 明確停止條件

- 缺 host context：`DEVICE_ENTRY_HOST_CONTEXT_REQUIRED`。
- 缺引用或嘗試用 caller route／環境替代：`DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED`。
- 引用目標在宿主未解除的拒絕清單：`DEVICE_ENTRY_TARGET_ACCESS_BLOCKED`。
- 同一實例已對該 request ID 開始呼叫：`DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED`。
- 準備結果被重建、跨實例或未通過：`DEVICE_ENTRY_PREPARATION_REQUIRED`。

同一實例的手機／電腦入口共用一次呼叫柵欄。UNKNOWN、FORBIDDEN、丟出的例外或
同時送出都不會觸發第二次 RPC。不能重建實例來重試未知事件，也不能改 nonce
重包同筆操作。跨程序持久的一次性保證仍由原 mailbox／宿主提供，這個模組不冒充它。

任何拒絕後都不得換入口、route、environment、帳戶、別名或工具繞過。
新入口不會自動退回原 ID-only self-report 模式。既有 self-report 功能仍維持原狀，
只能依它原有的授權、排除與查詢契約獨立使用。

## 目前沒有的能力

現行 RPC 沒有可信的 client/device metadata，也沒有取得任意當前 Work 官方引用的
通用 API。缺少宿主供應這些資料時須保留阻塞，不能由本模組憑空補出。
本地測試不代表兩個真設備已端到端測通，也不代表 FORBIDDEN 已解除。

## 原宿主的正式來源接入

`functions_host_device_entry_v1.mjs` 將前述準備層接到兩種已支援的來源。
不是讓普通 Work 取得宿主特權，也不自動建立任務或提交登記。

1. 使用者本來就要求宿主建立新 Work 時，宿主明確呼叫 `createWork`。此方法只呼叫
   原 `thread_management.create_thread` 一次，直接保存這次真實工具回件中的
   structured threadId，建立 cloud-task 引用。建立成功或未知回件都原樣保留。
   `taskCreated=false`、`task_created=false`、`status=unavailable`、工具錯誤或缺
   structured reference 均不安裝引用，也不自動再建立一個。
2. 使用者已提供正式 native chat reference 時，原宿主經受保護的
   `installNativeReference` 安裝其完整 threadId／environmentId。缺環境即停止。
   這個方法不是 RPC 參數，不解析 self-report、URL文字或設備標籤來冒官方引用。

```js
import {createOfficialDeviceHost} from './functions_host_device_entry_v1.mjs';

const host = createOfficialDeviceHost({tools, deniedThreadIds: currentUnresolvedAccessDenials});

// 只在使用者確實要求建立這個 Work 時呼叫；不是啟動預設動作。
const created = await host.createWork({creationKey: originalCreationOperation,
  createArguments: originalAuthorizedCreateArguments});
// created.officialResult 是原回件，未知時先查同筆，不換 key 重建。

// 或者，安裝使用者提供、由原宿主收到的完整正式原生引用。
// const referenceHandle = host.installNativeReference(actualOfficialChatReference);

// 等到本次登記的使用者授權與原契約均已滿足，再明確準備及提交。
const prepared = host.prepareMobile({referenceHandle: created.referenceHandle, requestJson});
// 電腦介面則用 host.prepareDesktop，正式資源 route 仍由引用決定。
if (prepared.status !== 'INPUTS_PREPARED_NOT_AUTHORIZED') return prepared;
const outcome = await host.submit(prepared);
```

此層不讀 Work，也不把建立 prompt 視為原生 USER 授權。之後仍由原持權接收端按
同一 locator 核對完整 USER 證據、來源、當前宣告及全部 Source 門檻。
若要 Work 有執行環境，建立參數須沿官方建立工具契約選用實際可用的環境；
本模組不猜環境、不擴權，也不為已建 Work 偷換計算環境。

主體、權限或安裝上下文失效時，原宿主必須呼叫 `invalidate()`；取得新拒絕時用
`blockTarget(threadId)`。這會使舊準備結果失效。這些是已實作的宿主 hook，
不是已自動訂閱平台撤權通知。整個 scope／handle 僅在當前實例有效，不冒稱跨回合
持久化。原正式 nonce／sideband／once 契約仍負責跨程序防重送。

`functions_device_host_bundle.mjs` 是同一組已測試程式的單檔版，供原 functions
宿主載入；移除底部兩個 `export ` 前綴即可取得 `createOfficialDeviceHost`。
載入或安裝本身沒有外部效果。不要在不同 functions cell 假裝保有同一物件。
