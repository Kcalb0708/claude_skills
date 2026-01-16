---
name: mv3-cross-origin
description: Chrome Extension Manifest V3 跨域请求解决方案。使用 Content Script Bridge 模式在目标域页面上下文发送请求，自动携带 Cookie，解决扩展页面无法直接发送跨域请求的问题。适用于"需要访问第三方 API 并携带认证信息"的场景。
---

# MV3 跨域通信技能

## 概述

Manifest V3 中，扩展页面（如 popup、dashboard）无法直接发送携带目标域 Cookie 的请求。本技能使用 **Content Script Bridge 模式** 解决此问题。

## 核心原理

```
┌─────────────────┐     chrome.runtime      ┌─────────────────┐
│  Extension Page │ ───────────────────────→ │  Service Worker │
│  (dashboard)    │                          │                 │
└─────────────────┘                          └────────┬────────┘
                                                      │
                                          chrome.tabs.sendMessage
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │  Content Script │
                                             │  (bridge.js)    │
                                             │  在目标域执行    │
                                             └────────┬────────┘
                                                      │
                                                fetch(url, { credentials: 'include' })
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │  目标域 API     │
                                             │  自动携带 Cookie │
                                             └─────────────────┘
```

## 实现步骤

### 1. manifest.json 配置

```json
{
  "manifest_version": 3,
  "permissions": ["scripting", "tabs"],
  "host_permissions": [
    "https://target-domain.com/*"
  ],
  "content_scripts": [
    {
      "matches": ["https://target-domain.com/*"],
      "js": ["content/bridge.js"],
      "run_at": "document_end"
    }
  ],
  "background": {
    "service_worker": "service_worker.js"
  }
}
```

### 2. Content Script Bridge (content/bridge.js)

```javascript
// bridge.js - 在目标域页面上下文执行

console.log('[Bridge] Loaded on', location.href);

// 监听来自 Service Worker 的消息
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 必须使用 async IIFE 保持消息通道开放
  (async () => {
    try {
      // Ping 检测 - 验证 bridge 是否就绪
      if (msg?.type === 'ping') {
        sendResponse({ ok: true, host: location.host });
        return;
      }

      // 处理 fetch 请求
      if (msg?.type === 'bridge-fetch') {
        const { url, method = 'POST', data, headers = {} } = msg.payload;

        const response = await fetch(url, {
          method,
          headers: {
            'Content-Type': 'application/json',
            ...headers
          },
          body: data ? JSON.stringify(data) : undefined,
          credentials: 'include'  // 关键：携带 Cookie
        });

        const json = await response.json();
        sendResponse({
          ok: response.ok,
          status: response.status,
          data: json
        });
      }
    } catch (error) {
      sendResponse({
        ok: false,
        error: error.message
      });
    }
  })();

  return true;  // 必须返回 true 保持消息通道开放
});
```

### 3. Service Worker 消息路由 (service_worker.js)

```javascript
// service_worker.js - 消息路由中心

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'BRIDGE_REQUEST') {
    handleBridgeRequest(message, sender, sendResponse);
    return true;  // 异步响应
  }
});

async function handleBridgeRequest(message, sender, sendResponse) {
  const { targetHost, requestData } = message;

  try {
    // 1. 查找目标域的标签页
    const tabs = await chrome.tabs.query({});
    const targetTab = tabs.find(tab => {
      try {
        return new URL(tab.url).hostname.includes(targetHost);
      } catch {
        return false;
      }
    });

    if (!targetTab) {
      sendResponse({
        success: false,
        error: `未找到 ${targetHost} 的标签页，请先登录`
      });
      return;
    }

    // 2. Ping 检测 Content Script 是否就绪（带重试）
    let pingSuccess = false;
    for (let i = 0; i < 10; i++) {
      try {
        const ping = await chrome.tabs.sendMessage(targetTab.id, { type: 'ping' });
        if (ping?.ok) {
          pingSuccess = true;
          break;
        }
      } catch {
        await new Promise(r => setTimeout(r, 500));
      }
    }

    if (!pingSuccess) {
      // 尝试重新注入 Content Script
      await chrome.scripting.executeScript({
        target: { tabId: targetTab.id },
        files: ['content/bridge.js']
      });
      await new Promise(r => setTimeout(r, 1000));
    }

    // 3. 发送实际请求
    const response = await chrome.tabs.sendMessage(targetTab.id, {
      type: 'bridge-fetch',
      payload: requestData
    });

    sendResponse({ success: true, data: response });

  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
}
```

### 4. 扩展页面调用 (api.js)

```javascript
// lib/api.js - API 服务层

class ApiService {
  async bridgeRequest(url, data, targetHost) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        type: 'BRIDGE_REQUEST',
        targetHost,
        requestData: {
          type: 'bridge-fetch',
          payload: { url, method: 'POST', data }
        }
      }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (response?.success) {
          resolve(response.data);
        } else {
          reject(new Error(response?.error || '请求失败'));
        }
      });
    });
  }

  // 使用示例
  async getSalesData() {
    return this.bridgeRequest(
      'https://api.example.com/sales',
      { pageSize: 100 },
      'api.example.com'
    );
  }
}
```

## 关键技术点

### 1. 消息通道保持开放

```javascript
// ❌ 错误：异步操作后通道已关闭
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  fetch(url).then(r => sendResponse(r));  // sendResponse 无效
});

// ✅ 正确：返回 true 保持通道开放
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const result = await fetch(url);
    sendResponse(result);  // 有效
  })();
  return true;  // 关键
});
```

### 2. Ping 重试机制

```javascript
// Content Script 可能未加载完成，需要重试
async function pingWithRetry(tabId, maxRetries = 10) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: 'ping' });
      if (response?.ok) return true;
    } catch {
      await new Promise(r => setTimeout(r, 500));
    }
  }
  return false;
}
```

### 3. 动态注入 Content Script

```javascript
// 连接失败时重新注入
if (error.message.includes('Could not establish connection')) {
  await chrome.scripting.executeScript({
    target: { tabId: targetTab.id },
    files: ['content/bridge.js']
  });
}
```

### 4. 多域名支持

```javascript
// 域名映射处理（同一系统多入口）
const domainMapping = {
  'api.example.com': ['api.example.com', 'api-us.example.com'],
  'api-us.example.com': ['api.example.com', 'api-us.example.com']
};

function findTargetTab(tabs, targetHost) {
  const domains = domainMapping[targetHost] || [targetHost];
  return tabs.find(tab => {
    const host = new URL(tab.url).hostname;
    return domains.some(d => host.includes(d));
  });
}
```

## 错误处理

### 常见错误及解决方案

| 错误 | 原因 | 解决方案 |
|------|------|---------|
| `Could not establish connection` | Content Script 未加载 | 动态注入 + 等待 |
| `Receiving end does not exist` | 标签页已关闭 | 重新打开 + 注入 |
| `No active tab found` | 用户未登录目标域 | 提示用户登录 |
| `credentials: include` 无效 | 跨域限制 | 必须在目标域 Content Script 中执行 |

### 错误恢复流程

```javascript
async function requestWithRecovery(targetHost, requestData) {
  try {
    return await bridgeRequest(requestData, targetHost);
  } catch (error) {
    if (error.message.includes('No active tab')) {
      // 打开目标域页面
      await chrome.tabs.create({ url: `https://${targetHost}` });
      // 等待用户登录
      await waitForLogin(targetHost);
      // 重试请求
      return await bridgeRequest(requestData, targetHost);
    }
    throw error;
  }
}
```

## 安全注意事项

1. **host_permissions 最小化**：只申请必需的域名权限
2. **验证消息来源**：检查 `sender.id === chrome.runtime.id`
3. **敏感数据处理**：避免在消息中传递密码等敏感信息
4. **Cookie 安全**：credentials: 'include' 仅在必要时使用

## 调试技巧

```javascript
// 在 Content Script 中
console.log('[Bridge]', location.host, 'received:', msg.type);

// 在 Service Worker 中
console.log('[SW] Routing to', targetHost, 'tab:', targetTab?.id);

// 检查 Content Script 是否已注入
chrome.tabs.sendMessage(tabId, { type: 'ping' })
  .then(r => console.log('Bridge ready:', r))
  .catch(e => console.log('Bridge not ready:', e));
```

## 使用场景

- 需要携带 Cookie 访问第三方 API（如 Temu、ERP 系统）
- 扩展页面无法直接跨域请求的场景
- 需要利用用户已登录状态的 API 调用

---

## declarativeNetRequest API（官方规范）

Manifest V3 推荐使用 declarativeNetRequest 替代 webRequest 进行网络请求拦截和修改。

### 核心能力

| 操作 | 说明 |
|------|------|
| `block` | 阻止网络请求 |
| `redirect` | 重定向请求 |
| `allow` | 允许请求（覆盖 block 规则） |
| `upgradeScheme` | 升级 http/ftp 到 https |
| `modifyHeaders` | 修改请求/响应头 |
| `allowAllRequests` | 允许框架层级内所有请求 |

### manifest.json 配置

```json
{
  "manifest_version": 3,
  "permissions": [
    "declarativeNetRequest",
    "declarativeNetRequestFeedback"  // 可选：调试用
  ],
  "declarative_net_request": {
    "rule_resources": [
      {
        "id": "ruleset_1",
        "enabled": true,
        "path": "rules/ruleset_1.json"
      }
    ]
  }
}
```

### 静态规则示例

```json
// rules/ruleset_1.json
[
  {
    "id": 1,
    "priority": 1,
    "action": { "type": "block" },
    "condition": {
      "urlFilter": "||ads.example.com/",
      "resourceTypes": ["script", "image"]
    }
  },
  {
    "id": 2,
    "priority": 1,
    "action": { "type": "allow" },
    "condition": {
      "urlFilter": "||ads.example.com/allowed",
      "resourceTypes": ["script"]
    }
  },
  {
    "id": 3,
    "priority": 1,
    "action": {
      "type": "redirect",
      "redirect": { "extensionPath": "/blocked.html" }
    },
    "condition": {
      "urlFilter": "||blocked-site.com/",
      "resourceTypes": ["main_frame"]
    }
  }
]
```

### 修改请求头

```json
{
  "id": 1,
  "priority": 1,
  "action": {
    "type": "modifyHeaders",
    "requestHeaders": [
      { "header": "cookie", "operation": "remove" },
      { "header": "X-Custom-Header", "operation": "set", "value": "custom-value" }
    ],
    "responseHeaders": [
      { "header": "X-Frame-Options", "operation": "remove" }
    ]
  },
  "condition": {
    "urlFilter": "||example.com/",
    "resourceTypes": ["main_frame", "sub_frame"]
  }
}
```

### 动态规则管理（Chrome 87+）

```javascript
// 获取现有动态规则
const oldRules = await chrome.declarativeNetRequest.getDynamicRules();
const oldRuleIds = oldRules.map(rule => rule.id);

// 更新动态规则（原子操作）
await chrome.declarativeNetRequest.updateDynamicRules({
  removeRuleIds: oldRuleIds,
  addRules: [
    {
      id: 1,
      priority: 1,
      action: { type: "block" },
      condition: {
        urlFilter: "||blocked-domain.com/",
        resourceTypes: ["script"]
      }
    }
  ]
});
```

### 规则数量限制

| 类型 | 限制 |
|------|------|
| 静态规则 | MAX_NUMBER_OF_STATIC_RULESETS（静态规则集总数） |
| 动态规则 | MAX_NUMBER_OF_DYNAMIC_RULES |
| 会话规则 | MAX_NUMBER_OF_SESSION_RULES |
| 不安全规则 | MAX_NUMBER_OF_UNSAFE_DYNAMIC_RULES |

### URL Filter 语法

```
||    - 匹配域名开始（任何协议）
|     - 匹配 URL 开始
^     - 分隔符（除字母数字和 _ - . % 外的字符）
*     - 通配符（匹配任意字符）

示例：
||example.com^     - 匹配 example.com 及其子域名
|https://          - 匹配 https 协议开始
*://*/api/*        - 匹配任何协议下的 /api/ 路径
```

---

## web_accessible_resources（Manifest V3）

Manifest V3 对 web_accessible_resources 进行了重大更改，增强了安全性控制。

### V2 vs V3 对比

```json
// Manifest V2 - 资源对所有网站开放
{
  "web_accessible_resources": [
    "images/*",
    "scripts/inject.js"
  ]
}

// Manifest V3 - 必须指定访问来源
{
  "web_accessible_resources": [
    {
      "resources": ["images/*"],
      "matches": ["*://*/*"]
    },
    {
      "resources": ["scripts/inject.js"],
      "matches": ["https://example.com/*"]
    }
  ]
}
```

### 完整配置选项

```json
{
  "web_accessible_resources": [
    {
      "resources": ["images/*", "styles/*.css"],
      "matches": ["https://trusted-site.com/*"],
      "extension_ids": [],
      "use_dynamic_url": false
    },
    {
      "resources": ["scripts/inject.js"],
      "matches": ["<all_urls>"],
      "use_dynamic_url": true
    },
    {
      "resources": ["shared-resource.js"],
      "extension_ids": ["abcdefghijklmnop", "qrstuvwxyz123456"]
    }
  ]
}
```

### 配置属性说明

| 属性 | 类型 | 说明 |
|------|------|------|
| `resources` | string[] | 资源路径（支持通配符 `*`） |
| `matches` | string[] | 允许访问的网站 URL 模式 |
| `extension_ids` | string[] | 允许访问的扩展 ID |
| `use_dynamic_url` | boolean | 使用动态 URL（每次会话/重载后变化） |

**注意**：每个对象必须包含 `resources` 和 `matches`/`extension_ids` 之一。

### 动态 URL 安全增强

```json
{
  "web_accessible_resources": [
    {
      "resources": ["sensitive-script.js"],
      "matches": ["https://example.com/*"],
      "use_dynamic_url": true
    }
  ]
}
```

启用 `use_dynamic_url` 后：
- 资源 URL 每次浏览器重启或扩展重载后变化
- 防止恶意网站缓存或预测扩展资源 URL
- 提高扩展资源的安全性

### 在 Content Script 中访问资源

```javascript
// content/bridge.js
const imageUrl = chrome.runtime.getURL('images/icon.png');
const scriptUrl = chrome.runtime.getURL('scripts/inject.js');

// 注入脚本到页面
const script = document.createElement('script');
script.src = scriptUrl;
document.head.appendChild(script);

// 加载图片
const img = document.createElement('img');
img.src = imageUrl;
document.body.appendChild(img);
```

### Favicon 资源示例

```json
{
  "permissions": ["favicon"],
  "web_accessible_resources": [
    {
      "resources": ["_favicon/*"],
      "matches": ["<all_urls>"],
      "extension_ids": ["*"]
    }
  ]
}
```

### 跨扩展资源共享

```json
// 扩展 A - 提供资源
{
  "web_accessible_resources": [
    {
      "resources": ["shared-lib.js"],
      "extension_ids": ["扩展B的ID", "扩展C的ID"]
    }
  ]
}

// 扩展 B - 访问资源
const sharedLibUrl = `chrome-extension://扩展A的ID/shared-lib.js`;
```

---

## 最佳实践

### 1. 权限最小化

```json
{
  "host_permissions": [
    "https://specific-api.example.com/*"  // ✅ 具体域名
    // "https://*/*"  // ❌ 避免过宽权限
  ],
  "web_accessible_resources": [
    {
      "resources": ["inject.js"],
      "matches": ["https://trusted-site.com/*"]  // ✅ 限制访问来源
    }
  ]
}
```

### 2. 结合使用多种技术

```javascript
// 1. declarativeNetRequest 用于请求拦截/修改
await chrome.declarativeNetRequest.updateDynamicRules({
  addRules: [{
    id: 1,
    action: {
      type: "modifyHeaders",
      requestHeaders: [{ header: "Origin", operation: "remove" }]
    },
    condition: { urlFilter: "||api.example.com/" }
  }]
});

// 2. Content Script Bridge 用于携带 Cookie 请求
const result = await bridgeRequest(apiUrl, data, 'api.example.com');

// 3. web_accessible_resources 用于资源注入
const injectedScript = chrome.runtime.getURL('scripts/page-hook.js');
```

### 3. 错误处理和降级

```javascript
async function makeRequest(url, data) {
  // 优先尝试直接请求（Service Worker）
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (response.ok) return response.json();
  } catch (e) {
    console.log('Direct fetch failed, falling back to bridge');
  }

  // 降级到 Bridge 模式
  return bridgeRequest(url, data, new URL(url).hostname);
}
```
