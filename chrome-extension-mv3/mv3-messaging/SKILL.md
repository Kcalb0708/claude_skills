---
name: mv3-messaging
description: Chrome Extension Manifest V3 消息通信模式。涵盖 Service Worker、Content Script、Extension Page 之间的双向通信，包括一次性消息、长连接、广播等模式。适用于"扩展各组件间需要通信"的场景。
---

# MV3 消息通信技能

## 概述

Chrome Extension 中有三种主要组件需要相互通信：
- **Service Worker**：后台服务，无法直接访问 DOM
- **Content Script**：注入到网页中，可访问 DOM 但隔离于页面 JS
- **Extension Page**：扩展自有页面（popup、dashboard 等）

## 通信架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Chrome Extension 消息架构                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐                      ┌──────────────┐         │
│  │ Extension    │  chrome.runtime      │   Service    │         │
│  │ Page         │◄────────────────────►│   Worker     │         │
│  │ (popup/dash) │  .sendMessage        │              │         │
│  └──────────────┘                      └──────┬───────┘         │
│         │                                     │                 │
│         │                                     │ chrome.tabs     │
│         │ chrome.runtime                      │ .sendMessage    │
│         │ .sendMessage                        │                 │
│         │                                     ▼                 │
│         │                              ┌──────────────┐         │
│         └─────────────────────────────►│  Content     │         │
│                                        │  Script      │         │
│                                        └──────────────┘         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 消息通信模式

### 模式1：一次性消息（最常用）

#### Extension Page → Service Worker

```javascript
// Extension Page 发送
chrome.runtime.sendMessage({
  type: 'GET_DATA',
  payload: { id: 123 }
}, (response) => {
  if (chrome.runtime.lastError) {
    console.error('Error:', chrome.runtime.lastError);
    return;
  }
  console.log('Response:', response);
});

// Promise 封装版本（推荐）
function sendToServiceWorker(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

// 使用
const data = await sendToServiceWorker({ type: 'GET_DATA', id: 123 });
```

#### Service Worker 接收并响应

```javascript
// service_worker.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('Received from:', sender.tab ? 'content script' : 'extension page');

  // 同步响应
  if (message.type === 'PING') {
    sendResponse({ success: true, time: Date.now() });
    return false;  // 同步响应返回 false 或不返回
  }

  // 异步响应 - 必须返回 true
  if (message.type === 'GET_DATA') {
    (async () => {
      try {
        const data = await fetchData(message.payload);
        sendResponse({ success: true, data });
      } catch (error) {
        sendResponse({ success: false, error: error.message });
      }
    })();
    return true;  // 关键：保持消息通道开放
  }
});
```

### 模式2：Service Worker → Content Script

```javascript
// Service Worker 发送到特定标签页
async function sendToContentScript(tabId, message) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, message);
    return response;
  } catch (error) {
    console.error(`Failed to send to tab ${tabId}:`, error);
    throw error;
  }
}

// 发送到所有匹配的标签页
async function broadcastToContentScripts(message, urlPattern = '*://*/*') {
  const tabs = await chrome.tabs.query({ url: urlPattern });
  const results = await Promise.allSettled(
    tabs.map(tab => chrome.tabs.sendMessage(tab.id, message))
  );
  return results;
}

// 使用示例
await sendToContentScript(tabId, { type: 'REFRESH_DATA' });
await broadcastToContentScripts({ type: 'CLEAR_CACHE' }, 'https://example.com/*');
```

### 模式3：Content Script 接收

```javascript
// content/bridge.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[Content Script] Received:', message.type);

  // 异步处理模式
  (async () => {
    try {
      switch (message.type) {
        case 'ping':
          sendResponse({ ok: true, host: location.host });
          break;

        case 'GET_PAGE_INFO':
          const info = {
            title: document.title,
            url: location.href,
            cookies: document.cookie
          };
          sendResponse({ success: true, data: info });
          break;

        case 'EXECUTE_ACTION':
          await performAction(message.payload);
          sendResponse({ success: true });
          break;

        default:
          sendResponse({ success: false, error: 'Unknown message type' });
      }
    } catch (error) {
      sendResponse({ success: false, error: error.message });
    }
  })();

  return true;  // 异步响应
});
```

### 模式4：消息路由器（推荐架构）

```javascript
// service_worker.js - 统一消息路由

const messageHandlers = {
  PING: handlePing,
  GET_DATA: handleGetData,
  BRIDGE_REQUEST: handleBridgeRequest,
  OPEN_TAB: handleOpenTab,
  FETCH_API: handleFetchAPI
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handler = messageHandlers[message.type];

  if (!handler) {
    console.warn('Unknown message type:', message.type);
    sendResponse({ success: false, error: 'Unknown message type' });
    return false;
  }

  // 调用对应处理器
  const result = handler(message, sender, sendResponse);

  // 如果处理器返回 Promise，保持通道开放
  if (result instanceof Promise || result === true) {
    return true;
  }

  return false;
});

// 处理器示例
function handlePing(message, sender, sendResponse) {
  sendResponse({ success: true, timestamp: Date.now() });
  return false;  // 同步响应
}

async function handleGetData(message, sender, sendResponse) {
  try {
    const data = await fetchFromAPI(message.params);
    sendResponse({ success: true, data });
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
  return true;  // 异步响应
}
```

### 模式5：长连接（Port）

#### Port 生命周期（官方规范）

| 事件 | 触发条件 |
|------|---------|
| **Port 创建** | 调用 `tabs.connect`、`runtime.connect` 或 `runtime.connectNative` |
| **多帧触发** | 如果标签页有多个 frame，`tabs.connect` 会为每个 frame 触发一次 `onConnect` |
| **onDisconnect** | 另一端没有监听器 / 标签页被卸载 / 所有接收 frame 已卸载 |

```javascript
// Extension Page 建立连接
const port = chrome.runtime.connect({ name: 'dashboard' });

// 发送消息
port.postMessage({ type: 'SUBSCRIBE', channel: 'updates' });

// 接收消息
port.onMessage.addListener((message) => {
  console.log('Received:', message);
});

// 连接断开
port.onDisconnect.addListener(() => {
  console.log('Disconnected from service worker');
  // 可选：检查是否有错误
  if (chrome.runtime.lastError) {
    console.error('Disconnect error:', chrome.runtime.lastError.message);
  }
});

// ---

// Service Worker 处理连接
const connectedPorts = new Set();

chrome.runtime.onConnect.addListener((port) => {
  console.log('New connection:', port.name);
  connectedPorts.add(port);

  port.onMessage.addListener((message) => {
    if (message.type === 'SUBSCRIBE') {
      // 处理订阅
    }
  });

  port.onDisconnect.addListener(() => {
    connectedPorts.delete(port);
    console.log('Connection closed:', port.name);
  });
});

// 广播给所有连接
function broadcast(message) {
  connectedPorts.forEach(port => {
    try {
      port.postMessage(message);
    } catch (e) {
      connectedPorts.delete(port);
    }
  });
}
```

#### 与其他扩展通信

```javascript
// 监听来自其他扩展的长连接
chrome.runtime.onConnectExternal.addListener((port) => {
  console.log('External connection from:', port.sender.id);

  port.onMessage.addListener((msg) => {
    // 处理来自其他扩展的消息
    console.log('External message:', msg);
  });
});

// 连接到其他扩展
const externalPort = chrome.runtime.connect('other-extension-id', {
  name: 'cross-extension'
});
externalPort.postMessage({ greeting: 'hello' });
```

#### 与 Native 应用通信

```javascript
// 需要 "nativeMessaging" 权限
const nativePort = chrome.runtime.connectNative('com.my_company.my_application');

nativePort.onMessage.addListener((msg) => {
  console.log('Received from native:', msg);
});

nativePort.onDisconnect.addListener(() => {
  console.log('Native app disconnected');
});

nativePort.postMessage({ text: 'Hello, native app' });
```

## 异步响应最佳实践

### ❌ 错误模式

```javascript
// 错误：异步操作完成前通道已关闭
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  fetch(url).then(response => {
    sendResponse(response);  // 无效！通道已关闭
  });
  // 没有 return true
});

// 错误：直接使用 async 函数
chrome.runtime.onMessage.addListener(async (msg, sender, sendResponse) => {
  const data = await fetchData();
  sendResponse(data);  // 无效！async 函数隐式返回 Promise
});
```

### ✅ 正确模式

```javascript
// 方式1：async IIFE + return true
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const data = await fetchData();
    sendResponse(data);
  })();
  return true;
});

// 方式2：独立异步处理函数
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessageAsync(msg, sendResponse);
  return true;
});

async function handleMessageAsync(msg, sendResponse) {
  try {
    const data = await fetchData();
    sendResponse({ success: true, data });
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
}
```

## 消息类型设计

```typescript
// 定义消息类型（TypeScript 或 JSDoc）
interface Message {
  type: string;
  payload?: any;
}

// 请求消息
interface RequestMessage extends Message {
  type: 'GET_DATA' | 'BRIDGE_REQUEST' | 'OPEN_TAB';
  requestId?: string;  // 用于关联请求和响应
}

// 响应消息
interface ResponseMessage {
  success: boolean;
  data?: any;
  error?: string;
  requestId?: string;
}

// 事件消息（广播）
interface EventMessage extends Message {
  type: 'DATA_UPDATED' | 'LOGIN_SUCCESS' | 'ERROR_OCCURRED';
  timestamp: number;
}
```

## 调试技巧

### 1. 消息日志

```javascript
// 在各组件入口添加日志
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log('[SW] Received:', {
    type: msg.type,
    from: sender.tab ? `tab:${sender.tab.id}` : 'extension',
    url: sender.tab?.url || sender.url
  });
  // ...
});
```

### 2. 检查消息通道状态

```javascript
function sendWithTimeout(message, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('Message timeout'));
    }, timeout);

    chrome.runtime.sendMessage(message, (response) => {
      clearTimeout(timer);
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(response);
      }
    });
  });
}
```

### 3. Service Worker 调试

```
1. 打开 chrome://extensions/
2. 找到扩展，点击 "Service Worker" 链接
3. 在 DevTools Console 中查看日志
```

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `Could not establish connection` | 接收方未注册监听器 | 确保监听器已注册 |
| `Receiving end does not exist` | 标签页已关闭或未加载 | 检查标签页状态 |
| `sendResponse 无效` | 未返回 true | 异步响应必须返回 true |
| `Message port closed` | 发送方已关闭 | 使用 try-catch 处理 |

## 使用场景

- 扩展页面请求 Service Worker 代理 API 调用
- Service Worker 通知 Content Script 执行 DOM 操作
- Content Script 上报页面事件给 Service Worker
- 多个扩展页面间数据同步

---

## Plasmo Messaging API

Plasmo 框架提供类型安全的消息通信封装，支持 Message Flow、Ports 和 Relay 三种模式。

### Message Flow（推荐）

REST 风格的消息处理器，自动路由到对应处理文件。

```typescript
// background/messages/ping.ts
import type { PlasmoMessaging } from "@plasmohq/messaging"

const handler: PlasmoMessaging.MessageHandler = async (req, res) => {
  const { id } = req.body
  const data = await queryAPI(id)
  res.send({ message: data })
}

export default handler
```

```typescript
// 在 popup/content script 中调用
import { sendToBackground } from "@plasmohq/messaging"

const resp = await sendToBackground({
  name: "ping",  // 对应 background/messages/ping.ts
  body: { id: 123 }
})
console.log(resp.message)
```

### sendToContentScript

从 Background 发送消息到 Content Script。

```typescript
// background/index.ts
import { sendToContentScript } from "@plasmohq/messaging"

const resp = await sendToContentScript({
  name: "get-page-data",
  tabId: 123  // 必须指定 tabId
})
```

```typescript
// contents/handler.ts
import type { PlasmoMessaging } from "@plasmohq/messaging"

export const config = {
  matches: ["https://example.com/*"]
}

const handler: PlasmoMessaging.MessageHandler = async (req, res) => {
  const pageTitle = document.title
  res.send({ title: pageTitle })
}

export default handler
```

### Ports（长连接）

```typescript
// background/ports/chat.ts
import type { PlasmoMessaging } from "@plasmohq/messaging"

const handler: PlasmoMessaging.PortHandler = async (req, res) => {
  console.log("Port message:", req.body)
  res.send({ received: true, timestamp: Date.now() })
}

export default handler
```

```typescript
// popup.tsx 或其他页面
import { usePort } from "@plasmohq/messaging/hook"

function ChatComponent() {
  const port = usePort("chat")

  const sendMessage = () => {
    port.send({ text: "Hello" })
  }

  return (
    <div>
      <button onClick={sendMessage}>发送</button>
      <p>响应: {JSON.stringify(port.data)}</p>
    </div>
  )
}
```

### Relay Flow（MAIN World 通信）

MAIN World 脚本无法直接访问 Chrome API，需通过 Relay 中继。

```typescript
// contents/relay.ts - ISOLATED World 中继脚本
import type { PlasmoCSConfig } from "plasmo"
import { relayMessage } from "@plasmohq/messaging"

export const config: PlasmoCSConfig = {
  matches: ["https://example.com/*"]
}

// 中继 "ping" 消息到 background
relayMessage({ name: "ping" })
```

```typescript
// contents/main-world.ts - MAIN World 脚本
import type { PlasmoCSConfig } from "plasmo"
import { sendToBackground } from "@plasmohq/messaging"

export const config: PlasmoCSConfig = {
  matches: ["https://example.com/*"],
  world: "MAIN"
}

// 需要指定 extensionId（因为在 MAIN world）
const resp = await sendToBackground({
  name: "ping",
  body: { id: 123 },
  extensionId: "your-extension-id"  // 从 chrome://extensions 获取
})
```

### Plasmo vs 原生消息对比

| 特性 | 原生 MV3 | Plasmo Messaging |
|------|----------|------------------|
| 消息路由 | 手动 switch/case | 自动文件路由 |
| 类型安全 | 需手动定义 | TypeScript 内置 |
| 响应方式 | sendResponse 回调 | res.send() 方法 |
| 长连接 | chrome.runtime.connect | usePort Hook |
| MAIN World | 复杂 postMessage | Relay 自动中继 |

### 目录结构

```
src/
├── background/
│   ├── index.ts           # Service Worker 入口
│   ├── messages/          # 消息处理器
│   │   ├── ping.ts        # → sendToBackground({ name: "ping" })
│   │   └── get-data.ts    # → sendToBackground({ name: "get-data" })
│   └── ports/             # 端口处理器
│       └── stream.ts      # → usePort("stream")
└── contents/
    └── relay.ts           # MAIN World 中继
```

### 注意事项

1. **文件命名**：处理器文件名即为消息名（ping.ts → name: "ping"）
2. **类型定义**：使用 `PlasmoMessaging.MessageHandler` 获得类型提示
3. **Relay 必需**：MAIN World 必须配合 Relay 脚本使用
4. **extensionId**：MAIN World 调用需显式传入扩展 ID
