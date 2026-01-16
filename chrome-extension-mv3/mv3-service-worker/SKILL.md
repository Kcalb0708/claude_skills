---
name: mv3-service-worker
description: Chrome Extension Manifest V3 Service Worker 开发模式。涵盖后台服务、定时任务、事件监听、API 代理、标签页管理等核心功能。适用于"需要后台处理逻辑"的扩展开发场景。
---

# MV3 Service Worker 技能

## 概述

Manifest V3 使用 Service Worker 替代了 MV2 的 Background Page。Service Worker 是事件驱动的，不会持久运行，需要特殊处理才能实现持久化任务。

## 核心特性

| 特性 | MV2 Background | MV3 Service Worker |
|------|----------------|-------------------|
| 生命周期 | 持久运行 | 事件驱动，空闲后休眠 |
| DOM 访问 | 可访问 | 不可访问 |
| 定时任务 | setTimeout/setInterval | chrome.alarms |
| 存储 | localStorage | chrome.storage / IndexedDB |
| 网络请求 | fetch | fetch（需注意休眠问题） |

## manifest.json 配置

```json
{
  "manifest_version": 3,
  "background": {
    "service_worker": "service_worker.js",
    "type": "module"  // 可选：启用 ES modules
  },
  "permissions": [
    "alarms",      // 定时任务
    "storage",     // 数据存储
    "tabs",        // 标签页管理
    "scripting"    // 动态注入脚本
  ]
}
```

## 基础结构

```javascript
// service_worker.js

console.log('Service Worker loaded');

// ============================================
// 1. 扩展安装/更新事件
// ============================================
chrome.runtime.onInstalled.addListener((details) => {
  console.log('Extension installed:', details.reason);

  if (details.reason === 'install') {
    // 首次安装：初始化数据、创建定时任务
    initializeExtension();
  } else if (details.reason === 'update') {
    // 更新：迁移数据、更新配置
    migrateData(details.previousVersion);
  }
});

// ============================================
// 2. 扩展启动事件
// ============================================
chrome.runtime.onStartup.addListener(() => {
  console.log('Browser started, extension activated');
  // 浏览器启动时执行初始化
});

// ============================================
// 3. 消息监听（核心）
// ============================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 消息路由处理
  handleMessage(message, sender, sendResponse);
  return true;  // 异步响应
});

// ============================================
// 4. 扩展图标点击
// ============================================
chrome.action.onClicked.addListener(async (tab) => {
  // 打开扩展页面或执行操作
  await openDashboard();
});

// ============================================
// 5. 标签页事件
// ============================================
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete') {
    // 页面加载完成
    handleTabLoaded(tabId, tab);
  }
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  // 标签页关闭
  handleTabClosed(tabId);
});
```

## 定时任务（chrome.alarms）

### API 限制（官方规范）

| 版本 | 限制 |
|------|------|
| Chrome 120+ | 最小间隔从 1 分钟降为 **30 秒**（`periodInMinutes: 0.5`） |
| Chrome 117+ | 最多 **500 个活跃 alarms**，超出会失败 |
| 设备睡眠 | alarms 继续运行但不会唤醒设备，唤醒后执行 |

### 创建定时任务

```javascript
// 创建一次性任务（5分钟后执行）
chrome.alarms.create('one-time-task', {
  delayInMinutes: 5
});

// 创建周期性任务（每30分钟执行）
chrome.alarms.create('periodic-task', {
  periodInMinutes: 30
});

// Chrome 120+ 支持 30 秒间隔
chrome.alarms.create('fast-periodic', {
  periodInMinutes: 0.5  // 30秒
});

// 创建每日任务（凌晨2点）
function scheduleDaily() {
  const now = new Date();
  const target = new Date();
  target.setHours(2, 0, 0, 0);

  // 如果今天2点已过，设置为明天
  if (target <= now) {
    target.setDate(target.getDate() + 1);
  }

  const delayMinutes = (target - now) / 1000 / 60;

  chrome.alarms.create('daily-sync', {
    delayInMinutes: delayMinutes,
    periodInMinutes: 24 * 60  // 每24小时重复
  });
}
```

### 监听定时任务

```javascript
chrome.alarms.onAlarm.addListener((alarm) => {
  console.log('Alarm triggered:', alarm.name);

  switch (alarm.name) {
    case 'daily-sync':
      performDailySync();
      break;
    case 'periodic-task':
      performPeriodicTask();
      break;
    case 'one-time-task':
      performOneTimeTask();
      break;
  }
});

async function performDailySync() {
  try {
    console.log('Executing daily sync...');
    // 执行同步逻辑
    await syncData();
    // 通知扩展页面
    notifyPages({ type: 'SYNC_COMPLETE' });
  } catch (error) {
    console.error('Daily sync failed:', error);
  }
}
```

### 管理定时任务

```javascript
// 获取所有定时任务
const alarms = await chrome.alarms.getAll();
console.log('Active alarms:', alarms);

// 获取特定任务
const alarm = await chrome.alarms.get('daily-sync');

// 取消特定任务
await chrome.alarms.clear('daily-sync');

// 取消所有任务
await chrome.alarms.clearAll();
```

## 标签页管理

### 查询标签页

```javascript
// 查询所有标签页
const allTabs = await chrome.tabs.query({});

// 查询特定 URL 的标签页
const matchingTabs = await chrome.tabs.query({
  url: 'https://example.com/*'
});

// 查询当前活动标签页
const [activeTab] = await chrome.tabs.query({
  active: true,
  currentWindow: true
});

// 查找特定域名的标签页
function findTabByHost(hostname) {
  return chrome.tabs.query({}).then(tabs =>
    tabs.find(tab => {
      try {
        return new URL(tab.url).hostname.includes(hostname);
      } catch {
        return false;
      }
    })
  );
}
```

### 创建和管理标签页

```javascript
// 创建新标签页
const newTab = await chrome.tabs.create({
  url: 'https://example.com',
  active: false  // 后台打开
});

// 更新标签页
await chrome.tabs.update(tabId, {
  url: 'https://example.com/new-page',
  active: true
});

// 切换到标签页
async function focusTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  await chrome.tabs.update(tabId, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
}

// 关闭标签页
await chrome.tabs.remove(tabId);

// 打开扩展页面（单例模式）
async function openDashboard() {
  const dashboardUrl = chrome.runtime.getURL('pages/dashboard/index.html');

  // 查找已存在的页面
  const tabs = await chrome.tabs.query({});
  const existingTab = tabs.find(t => t.url?.startsWith(dashboardUrl));

  if (existingTab) {
    // 切换到已存在的页面
    await focusTab(existingTab.id);
  } else {
    // 创建新页面
    await chrome.tabs.create({ url: dashboardUrl, active: true });
  }
}
```

## API 代理（绕过 CORS）

```javascript
// Service Worker 可以绑过 CORS 限制发送请求
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'PROXY_FETCH') {
    proxyFetch(message, sendResponse);
    return true;
  }
});

async function proxyFetch(message, sendResponse) {
  try {
    const response = await fetch(message.url, {
      method: message.method || 'GET',
      headers: message.headers || {},
      body: message.body
    });

    const data = await response.json();
    sendResponse({
      success: true,
      status: response.status,
      data
    });
  } catch (error) {
    sendResponse({
      success: false,
      error: error.message
    });
  }
}
```

## 动态注入脚本

```javascript
// 注入 Content Script
async function injectContentScript(tabId, files) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files
    });
    console.log('Script injected successfully');
  } catch (error) {
    console.error('Injection failed:', error);
  }
}

// 注入并执行函数
async function executeFunction(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func,
    args
  });
  return results[0]?.result;
}

// 示例：获取页面标题
const title = await executeFunction(tabId, () => document.title);

// 示例：执行 DOM 操作
await executeFunction(tabId, (selector) => {
  const el = document.querySelector(selector);
  return el?.textContent;
}, ['.main-content']);
```

## 事件监听模式

### URL 变化监听

```javascript
// 监听标签页 URL 变化
const watchingTabs = new Map();

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!watchingTabs.has(tabId)) return;

  const prevUrl = watchingTabs.get(tabId).lastUrl;
  const newUrl = tab.url;

  if (changeInfo.url && prevUrl !== newUrl) {
    console.log(`Tab ${tabId} URL changed: ${prevUrl} → ${newUrl}`);
    watchingTabs.set(tabId, { lastUrl: newUrl });

    // 检测登录成功等场景
    if (isLoginSuccessUrl(newUrl)) {
      handleLoginSuccess(tabId);
    }
  }
});

// 开始监听某个标签页
function watchTab(tabId) {
  chrome.tabs.get(tabId).then(tab => {
    watchingTabs.set(tabId, { lastUrl: tab.url });
  });
}

// 停止监听
function unwatchTab(tabId) {
  watchingTabs.delete(tabId);
}
```

### 网络请求监听

```javascript
// 需要 webRequest 权限
chrome.webRequest.onCompleted.addListener(
  (details) => {
    console.log('Request completed:', details.url);
  },
  { urls: ['https://api.example.com/*'] }
);
```

## 状态持久化

### 使用 chrome.storage

```javascript
// 保存状态
async function saveState(state) {
  await chrome.storage.local.set({ appState: state });
}

// 读取状态
async function loadState() {
  const result = await chrome.storage.local.get('appState');
  return result.appState || {};
}

// 监听状态变化
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace === 'local' && changes.appState) {
    console.log('State changed:', changes.appState.newValue);
  }
});
```

## 错误处理

```javascript
// 全局错误处理
self.addEventListener('error', (event) => {
  console.error('Uncaught error:', event.error);
});

self.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled rejection:', event.reason);
});

// 消息处理错误包装
function safeHandler(handler) {
  return async (message, sender, sendResponse) => {
    try {
      await handler(message, sender, sendResponse);
    } catch (error) {
      console.error('Handler error:', error);
      sendResponse({ success: false, error: error.message });
    }
  };
}
```

## Service Worker 生命周期详解（官方规范）

### 终止条件

Chrome 会在以下情况终止 Service Worker：

| 条件 | 说明 |
|------|------|
| **30 秒无活动** | 没有接收事件或调用扩展 API 后自动终止 |
| **单个请求超时** | 单个事件或 API 调用处理超过 5 分钟 |
| **手动停止** | 用户在 chrome://serviceworker-internals 手动停止 |

### 1. 避免全局变量丢失

```javascript
// ❌ 错误：Service Worker 休眠后变量丢失
let cache = {};

// ✅ 正确：使用持久化存储
async function getCache() {
  const result = await chrome.storage.local.get('cache');
  return result.cache || {};
}

async function setCache(data) {
  await chrome.storage.local.set({ cache: data });
}

// ✅ 推荐：使用 chrome.storage.session（Service Worker 专用）
// 数据在内存中，扩展卸载/更新时清除
async function getSessionCache() {
  const result = await chrome.storage.session.get('cache');
  return result.cache || {};
}
```

### 2. waitUntil() 模式（官方推荐）

长时间运行的操作如果不调用扩展 API，需要定期调用以重置超时计时器：

```javascript
/**
 * 官方推荐的 waitUntil 辅助函数
 * 通过定期调用扩展 API 保持 Service Worker 活跃
 */
async function waitUntil(promise) {
  const keepAlive = setInterval(chrome.runtime.getPlatformInfo, 25 * 1000);
  try {
    await promise;
  } finally {
    clearInterval(keepAlive);
  }
}

// 使用示例
waitUntil(someExpensiveCalculation());
waitUntil(fetchLargeDataSet());
```

### 3. Heartbeat 模式（企业级方案）

适用于需要持续运行的企业或教育场景：

```javascript
/**
 * Heartbeat 机制 - 通过定期写入存储保持活跃
 * 注意：应谨慎使用，仅在确实需要持续运行时启用
 */
let heartbeatInterval;

async function runHeartbeat() {
  await chrome.storage.local.set({ 'last-heartbeat': new Date().getTime() });
}

async function startHeartbeat() {
  // 立即执行一次
  await runHeartbeat();
  // 每 20 秒执行一次
  heartbeatInterval = setInterval(runHeartbeat, 20 * 1000);
}

async function stopHeartbeat() {
  clearInterval(heartbeatInterval);
}

async function getLastHeartbeat() {
  return (await chrome.storage.local.get('last-heartbeat'))['last-heartbeat'];
}
```

### 4. 保持 Service Worker 活跃（Alarm 方案）

```javascript
// Chrome 120+ 支持最小 30 秒间隔
chrome.alarms.create('keep-alive', { periodInMinutes: 0.5 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keep-alive') {
    // 仅保持活跃，不执行实际操作
    console.log('Keep alive ping');
  }
});
```

### 5. 长任务处理

```javascript
// 长任务分块处理 + waitUntil 组合
async function processLargeData(items) {
  const BATCH_SIZE = 100;

  // 使用 waitUntil 包装整个长任务
  await waitUntil((async () => {
    for (let i = 0; i < items.length; i += BATCH_SIZE) {
      const batch = items.slice(i, i + BATCH_SIZE);
      await processBatch(batch);

      // 进度通知
      notifyProgress(i / items.length);

      // 让出执行时间
      await new Promise(r => setTimeout(r, 0));
    }
  })());
}
```

## 调试技巧

```
1. 打开 chrome://extensions/
2. 开启开发者模式
3. 点击 "Service Worker" 链接打开 DevTools
4. 在 Console 中查看日志
5. 在 Application → Service Workers 中查看状态
```

## 使用场景

- 消息路由中心
- 定时数据同步
- API 请求代理
- 标签页管理
- 事件监听和广播
- 扩展生命周期管理
