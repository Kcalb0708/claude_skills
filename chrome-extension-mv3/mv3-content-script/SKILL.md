---
name: mv3-content-script
description: Chrome Extension Manifest V3 Content Script 开发模式。涵盖 DOM 操作、页面数据提取、事件监听、与 Service Worker 通信、动态注入等技术。适用于"需要与网页交互"的扩展开发场景。
---

# MV3 Content Script 技能

## 概述

Content Script 是注入到网页中执行的脚本，可以访问和操作页面 DOM，但运行在隔离的 JavaScript 环境中，与页面原生 JS 相互隔离。

## 核心特性

| 能力 | 说明 |
|------|------|
| DOM 访问 | ✅ 完全访问 |
| 页面 JS 变量 | ❌ 隔离，无法直接访问（可通过 MAIN world 访问） |
| Chrome APIs | 部分可用（runtime、storage 等） |
| 网络请求 | ✅ 可发送，自动携带页面 Cookie |
| window 对象 | 隔离的 window（非页面 window） |

## 执行世界（World）- 官方规范

### ISOLATED vs MAIN

| 世界 | 说明 | 页面 JS 访问 | 安全性 |
|------|------|-------------|--------|
| **ISOLATED**（默认） | 隔离执行环境，与页面 JS 分离 | ❌ 无法访问 | ✅ 安全 |
| **MAIN** | 与页面共享执行环境 | ✅ 可直接访问 | ⚠️ 页面可干扰 |

### manifest.json 中配置 world

```json
{
  "content_scripts": [
    {
      "matches": ["https://example.com/*"],
      "js": ["content/bridge.js"],
      "run_at": "document_end",
      "world": "ISOLATED"
    },
    {
      "matches": ["https://example.com/*"],
      "js": ["content/page-inject.js"],
      "run_at": "document_start",
      "world": "MAIN"
    }
  ]
}
```

### 动态注入到 MAIN 世界（Chrome 95+）

```javascript
// Service Worker 中注入到页面主世界
await chrome.scripting.executeScript({
  target: { tabId: tab.id },
  world: 'MAIN',  // 关键：注入到页面 JS 环境
  func: () => {
    // 这里可以直接访问页面全局变量
    console.log('Page variable:', window.someGlobalVar);
    document.body.style.backgroundColor = 'lightblue';
  }
});

// 注入文件到 MAIN 世界
await chrome.scripting.executeScript({
  target: { tabId: tab.id },
  world: 'MAIN',
  files: ['content/page-script.js']
});
```

### MAIN 世界安全警告

```javascript
// ⚠️ 风险：页面可以覆盖原生方法
// 恶意页面可能这样做：
// window.fetch = () => Promise.resolve({ json: () => ({ hacked: true }) });

// ✅ 在 MAIN 世界中的防护措施
(function() {
  // 保存原生方法引用
  const originalFetch = window.fetch.bind(window);
  const originalXHR = window.XMLHttpRequest;

  // 使用保存的引用
  originalFetch('/api/data').then(...);
})();
```

## manifest.json 配置

### 静态注入（推荐）

```json
{
  "content_scripts": [
    {
      "matches": ["https://example.com/*", "https://*.example.com/*"],
      "js": ["content/bridge.js"],
      "css": ["content/styles.css"],
      "run_at": "document_end",
      "all_frames": false,
      "world": "ISOLATED"
    }
  ]
}
```

### run_at 时机

| 值 | 说明 | 适用场景 |
|----|------|---------|
| `document_start` | DOM 开始构建前 | 注入早期脚本 |
| `document_end` | DOM 构建完成，资源未加载 | 常用，推荐 |
| `document_idle` | 页面完全加载后 | 非关键操作 |

## 基础结构

```javascript
// content/bridge.js

console.log('[Content Script] Loaded on', location.href);

// ============================================
// 1. DOM 操作
// ============================================
function getPageData() {
  return {
    title: document.title,
    url: location.href,
    // 提取页面特定数据
    userInfo: extractUserInfo(),
    tableData: extractTableData()
  };
}

function extractUserInfo() {
  const userDiv = document.querySelector('.user-info');
  return userDiv?.textContent?.trim() || '';
}

function extractTableData() {
  const rows = document.querySelectorAll('table.data-table tr');
  return Array.from(rows).map(row => {
    const cells = row.querySelectorAll('td');
    return Array.from(cells).map(cell => cell.textContent.trim());
  });
}

// ============================================
// 2. 消息监听
// ============================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      const result = await handleMessage(message);
      sendResponse(result);
    } catch (error) {
      sendResponse({ success: false, error: error.message });
    }
  })();
  return true;
});

async function handleMessage(message) {
  switch (message.type) {
    case 'ping':
      return { ok: true, host: location.host };

    case 'GET_PAGE_DATA':
      return { success: true, data: getPageData() };

    case 'CLICK_ELEMENT':
      return clickElement(message.selector);

    case 'FILL_FORM':
      return fillForm(message.data);

    case 'FETCH_API':
      return await fetchWithCookie(message.payload);

    default:
      return { success: false, error: 'Unknown message type' };
  }
}

// ============================================
// 3. 发送请求（携带页面 Cookie）
// ============================================
async function fetchWithCookie(payload) {
  const { url, method = 'POST', data, headers = {} } = payload;

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
  return {
    ok: response.ok,
    status: response.status,
    data: json
  };
}
```

## DOM 操作模式

### 元素查找

```javascript
// 单个元素
const element = document.querySelector('.class-name');
const byId = document.getElementById('element-id');

// 多个元素
const elements = document.querySelectorAll('.item');
const elementsArray = Array.from(elements);

// 等待元素出现
function waitForElement(selector, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const element = document.querySelector(selector);
    if (element) {
      resolve(element);
      return;
    }

    const observer = new MutationObserver((mutations, obs) => {
      const element = document.querySelector(selector);
      if (element) {
        obs.disconnect();
        resolve(element);
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });

    setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Element ${selector} not found within ${timeout}ms`));
    }, timeout);
  });
}

// 使用
const button = await waitForElement('.submit-button');
```

### 元素操作

```javascript
// 点击元素
function clickElement(selector) {
  const element = document.querySelector(selector);
  if (!element) {
    return { success: false, error: `Element not found: ${selector}` };
  }

  element.click();
  return { success: true };
}

// 填充表单
function fillForm(data) {
  for (const [selector, value] of Object.entries(data)) {
    const input = document.querySelector(selector);
    if (input) {
      input.value = value;
      // 触发 input 事件（某些框架需要）
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  return { success: true };
}

// 滚动到元素
function scrollToElement(selector) {
  const element = document.querySelector(selector);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return { success: true };
  }
  return { success: false, error: 'Element not found' };
}
```

### DOM 变化监听

```javascript
// 监听 DOM 变化
function observeDOM(targetSelector, callback) {
  const target = document.querySelector(targetSelector);
  if (!target) return null;

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        callback(mutation.addedNodes, mutation.removedNodes);
      }
    }
  });

  observer.observe(target, {
    childList: true,
    subtree: true
  });

  return observer;
}

// 使用：监听表格数据变化
const observer = observeDOM('.data-table tbody', (added, removed) => {
  console.log('Table changed:', added.length, 'added,', removed.length, 'removed');
  // 通知 Service Worker
  chrome.runtime.sendMessage({
    type: 'TABLE_UPDATED',
    count: document.querySelectorAll('.data-table tbody tr').length
  });
});

// 停止监听
// observer.disconnect();
```

## 与页面 JS 通信

Content Script 与页面 JS 运行在隔离环境，需要通过 DOM 事件或 window.postMessage 通信。

### 方式1：自定义事件

```javascript
// Content Script 发送
function sendToPage(type, data) {
  const event = new CustomEvent('extension-message', {
    detail: { type, data }
  });
  document.dispatchEvent(event);
}

// Content Script 接收
document.addEventListener('page-message', (event) => {
  const { type, data } = event.detail;
  console.log('From page:', type, data);
});

// ---

// 页面 JS 发送
document.dispatchEvent(new CustomEvent('page-message', {
  detail: { type: 'SOME_EVENT', data: { foo: 'bar' } }
}));

// 页面 JS 接收
document.addEventListener('extension-message', (event) => {
  console.log('From extension:', event.detail);
});
```

### 方式2：注入脚本到页面上下文

```javascript
// 注入脚本到页面 JS 上下文
function injectScript(code) {
  const script = document.createElement('script');
  script.textContent = code;
  (document.head || document.documentElement).appendChild(script);
  script.remove();
}

// 示例：获取页面全局变量
injectScript(`
  window.postMessage({
    type: 'PAGE_VAR',
    data: window.someGlobalVar
  }, '*');
`);

// Content Script 接收
window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  if (event.data.type === 'PAGE_VAR') {
    console.log('Page variable:', event.data.data);
  }
});
```

## 动态注入

### 通过 Service Worker 注入

```javascript
// service_worker.js
async function injectContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content/bridge.js']
  });
}

// 注入 CSS
async function injectStyles(tabId) {
  await chrome.scripting.insertCSS({
    target: { tabId },
    files: ['content/styles.css']
  });
}

// 注入并执行函数
async function executeInPage(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func,
    args
  });
  return results[0]?.result;
}
```

### 条件注入

```javascript
// 只在特定条件下注入
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;

  // 检查 URL 匹配
  if (!tab.url?.includes('example.com')) return;

  // 检查是否已注入
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'ping' });
    console.log('Already injected');
  } catch {
    // 未注入，执行注入
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content/bridge.js']
    });
  }
});
```

## 数据提取模式

### 表格数据提取

```javascript
function extractTable(tableSelector) {
  const table = document.querySelector(tableSelector);
  if (!table) return [];

  const headers = Array.from(table.querySelectorAll('thead th'))
    .map(th => th.textContent.trim());

  const rows = Array.from(table.querySelectorAll('tbody tr'))
    .map(tr => {
      const cells = Array.from(tr.querySelectorAll('td'))
        .map(td => td.textContent.trim());

      // 转换为对象
      return headers.reduce((obj, header, i) => {
        obj[header] = cells[i];
        return obj;
      }, {});
    });

  return rows;
}
```

### 分页数据提取

```javascript
async function extractAllPages(tableSelector, nextSelector) {
  const allData = [];

  while (true) {
    // 提取当前页数据
    const pageData = extractTable(tableSelector);
    allData.push(...pageData);

    // 查找下一页按钮
    const nextButton = document.querySelector(nextSelector);
    if (!nextButton || nextButton.disabled) {
      break;
    }

    // 点击下一页
    nextButton.click();

    // 等待数据加载
    await new Promise(r => setTimeout(r, 1000));
  }

  return allData;
}
```

## 存储访问

```javascript
// Content Script 可以使用 chrome.storage
async function saveToStorage(key, value) {
  await chrome.storage.local.set({ [key]: value });
}

async function getFromStorage(key) {
  const result = await chrome.storage.local.get(key);
  return result[key];
}

// 使用 localStorage（页面域的 localStorage）
function saveToLocalStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function getFromLocalStorage(key) {
  const value = localStorage.getItem(key);
  return value ? JSON.parse(value) : null;
}
```

## 错误处理

```javascript
// 包装所有操作
async function safeExecute(operation, fallback = null) {
  try {
    return await operation();
  } catch (error) {
    console.error('[Content Script Error]', error);
    return fallback;
  }
}

// 消息处理错误包装
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      const result = await handleMessage(message);
      sendResponse({ success: true, ...result });
    } catch (error) {
      console.error('Message handling error:', error);
      sendResponse({
        success: false,
        error: error.message,
        stack: error.stack
      });
    }
  })();
  return true;
});
```

## 调试技巧

```javascript
// 1. 添加前缀日志
console.log('[Content Script]', location.host, 'message:', message);

// 2. 在页面 Console 中测试选择器
document.querySelector('.target-element');

// 3. 检查 Content Script 是否已加载
chrome.tabs.sendMessage(tabId, { type: 'ping' })
  .then(r => console.log('Loaded:', r))
  .catch(e => console.log('Not loaded:', e));

// 4. 在 DevTools 中查看 Content Script
// Sources → Content scripts → 扩展名
```

## 注意事项

1. **执行时机**：确保 DOM 已就绪再操作
2. **隔离环境**：无法直接访问页面 JS 变量
3. **CSP 限制**：某些页面可能限制脚本执行
4. **性能影响**：避免频繁 DOM 操作
5. **内存泄漏**：及时移除事件监听器和 Observer

## 使用场景

- 提取页面数据（表格、表单等）
- 自动化页面操作（填表、点击）
- 发送携带 Cookie 的请求
- 监听页面 DOM 变化
- 与 Service Worker 通信桥接

---

## Plasmo CSUI（Content Scripts UI）

Plasmo 框架提供 CSUI 功能，可在网页中注入 React/Vue/Svelte 组件，自动使用 Shadow DOM 隔离样式。

### 快速开始

```bash
# 创建 Plasmo 项目
pnpm create plasmo --with-src
cd my-extension && pnpm dev
```

### 基础 CSUI 组件

```tsx
// src/contents/inline.tsx
import type { PlasmoCSConfig } from "plasmo"

export const config: PlasmoCSConfig = {
  matches: ["https://example.com/*"],
  css: ["font.css"]
}

export default function InlineComponent() {
  return (
    <div style={{ padding: 12, background: "#fff" }}>
      <h1>Hello from Plasmo CSUI!</h1>
    </div>
  )
}
```

### PlasmoCSConfig 配置

```typescript
export const config: PlasmoCSConfig = {
  matches: ["https://*.example.com/*"],
  exclude_matches: ["*://example.com/admin/*"],
  all_frames: true,
  run_at: "document_end",
  world: "ISOLATED",  // ISOLATED | MAIN
  css: ["styles.css"]
}
```

### 锚点定位

```tsx
// src/contents/price-display.tsx
import type { PlasmoGetInlineAnchor, PlasmoGetInlineAnchorList } from "plasmo"

// 单个锚点
export const getInlineAnchor: PlasmoGetInlineAnchor = async () =>
  document.querySelector("#product-price")

// 多个锚点
export const getInlineAnchorList: PlasmoGetInlineAnchorList = async () => {
  const anchors = document.querySelectorAll(".product-item")
  return Array.from(anchors).map((element) => ({
    element,
    insertPosition: "afterend"  // beforebegin | afterbegin | beforeend | afterend
  }))
}

export default function PriceDisplay() {
  return <span className="plasmo-price">¥99.00</span>
}
```

### 覆盖层与自定义容器

```tsx
import type { PlasmoGetOverlayAnchor, PlasmoGetShadowHostId } from "plasmo"

// 覆盖层锚点（固定定位）
export const getOverlayAnchor: PlasmoGetOverlayAnchor = async () =>
  document.body

// 自定义 Shadow Host ID
export const getShadowHostId = () => "plasmo-overlay-container"

// 自定义根容器
export const getRootContainer = () => {
  const container = document.createElement("div")
  container.id = "my-plasmo-root"
  document.body.appendChild(container)
  return container
}

export default function Overlay() {
  return (
    <div style={{ position: "fixed", bottom: 20, right: 20 }}>
      <button>浮动按钮</button>
    </div>
  )
}
```

### 自定义样式注入

```tsx
import type { PlasmoGetStyle } from "plasmo"
import styleText from "data-text:./styles.css"

// 方式1：内联样式
export const getStyle: PlasmoGetStyle = () => {
  const style = document.createElement("style")
  style.textContent = `.plasmo-container { padding: 16px; }`
  return style
}

// 方式2：导入 CSS 文件
export const getStyle: PlasmoGetStyle = () => {
  const style = document.createElement("style")
  style.textContent = styleText
  return style
}
```

### MAIN World 注入

```tsx
import type { PlasmoCSConfig } from "plasmo"

export const config: PlasmoCSConfig = {
  matches: ["https://example.com/*"],
  world: "MAIN"  // 在页面 JS 上下文执行
}

export default function MainWorldComponent() {
  const pageData = (window as any).pageConfig
  return <div>页面配置: {JSON.stringify(pageData)}</div>
}
```

### Plasmo vs 原生对比

| 特性 | 原生 MV3 | Plasmo CSUI |
|------|----------|-------------|
| UI 注入 | 手动操作 DOM | 声明式 React 组件 |
| 样式隔离 | 需手动实现 | Shadow DOM 自动隔离 |
| 热更新 | 需手动刷新 | `pnpm dev` 自动热更新 |
| 定位方式 | querySelector | getInlineAnchor |

### 注意事项

1. **文件位置**：CSUI 文件必须放在 `src/contents/` 目录
2. **默认导出**：必须 `export default` 导出组件
3. **Shadow DOM**：样式默认隔离，需用 `getStyle` 注入
4. **多实例**：用 `getInlineAnchorList` 在多位置注入
