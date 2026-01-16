---
name: mv3-offscreen
description: Chrome Extension Manifest V3 Offscreen Document API。允许 Service Worker 通过隐藏文档访问 DOM、剪贴板、音视频等 Web API。适用于"需要在后台执行 DOM 操作"的扩展开发场景。
---

# MV3 Offscreen Document 技能

## 概述

Manifest V3 的 Service Worker 无法直接访问 DOM。Offscreen Document API 允许扩展创建隐藏的离屏文档来执行需要 DOM 访问或其他 Service Worker 不支持的 Web API 操作。

## 核心特性

| 特性 | 说明 |
|------|------|
| DOM 访问 | ✅ 完全访问 |
| Web API | ✅ 支持剪贴板、音视频、WebRTC 等 |
| 文档数量 | ⚠️ 每个扩展同时只能有一个 Offscreen 文档 |
| 生命周期 | 需要手动管理（创建和关闭） |
| 通信方式 | chrome.runtime.sendMessage |

## manifest.json 配置

```json
{
  "manifest_version": 3,
  "permissions": [
    "offscreen"
  ],
  "background": {
    "service_worker": "service_worker.js",
    "type": "module"
  }
}
```

## Reason 枚举值（官方规范）

创建 Offscreen 文档时必须指定 `reasons`，说明创建原因：

| Reason | 说明 |
|--------|------|
| `TESTING` | 仅用于测试目的 |
| `AUDIO_PLAYBACK` | 播放音频 |
| `IFRAME_SCRIPTING` | 嵌入并脚本操作 iframe 内容 |
| `DOM_SCRAPING` | 嵌入 iframe 并解析 DOM 提取信息 |
| `BLOBS` | 操作 Blob 对象（包括 `URL.createObjectURL()`） |
| `DOM_PARSER` | 使用 DOMParser API |
| `USER_MEDIA` | 操作用户媒体流（如 `getUserMedia()`） |
| `DISPLAY_MEDIA` | 操作显示媒体流（如 `getDisplayMedia()`） |
| `WEB_RTC` | 使用 WebRTC API |
| `CLIPBOARD` | 操作剪贴板 API |
| `LOCAL_STORAGE` | 访问 localStorage |
| `WORKERS` | 创建 Web Workers |
| `BATTERY_STATUS` | 使用 `navigator.getBattery` |
| `MATCH_MEDIA` | 使用 `window.matchMedia` |
| `GEOLOCATION` | 使用 `navigator.geolocation` |

## API 方法

### createDocument

```javascript
// 创建 Offscreen 文档
await chrome.offscreen.createDocument({
  url: 'offscreen.html',           // 相对于扩展根目录的 HTML 文件路径
  reasons: ['CLIPBOARD'],          // 创建原因数组
  justification: '需要访问剪贴板'   // 开发者说明（可能显示给用户）
});
```

### closeDocument

```javascript
// 关闭当前 Offscreen 文档
await chrome.offscreen.closeDocument();
```

### 检查文档是否存在（Chrome 116+）

```javascript
// Chrome 116+ 使用 runtime.getContexts
async function hasOffscreenDocument(path) {
  const offscreenUrl = chrome.runtime.getURL(path);

  if ('getContexts' in chrome.runtime) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
      documentUrls: [offscreenUrl]
    });
    return contexts.length > 0;
  }

  return false;
}
```

### 检查文档是否存在（Chrome 116 之前）

```javascript
// Chrome 116 之前使用 clients.matchAll
async function hasOffscreenDocument(path) {
  const offscreenUrl = chrome.runtime.getURL(path);

  if ('getContexts' in chrome.runtime) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
      documentUrls: [offscreenUrl]
    });
    return contexts.length > 0;
  } else {
    // 兼容旧版本
    const matchedClients = await clients.matchAll();
    return matchedClients.some(client => client.url === offscreenUrl);
  }
}
```

## 生命周期管理（官方推荐模式）

### 确保文档存在

```javascript
// service_worker.js
const OFFSCREEN_DOCUMENT_PATH = 'offscreen.html';
let creating = null;  // 全局 Promise，防止并发创建

/**
 * 确保 Offscreen 文档存在
 * 官方推荐的生命周期管理模式
 */
async function setupOffscreenDocument(path) {
  const offscreenUrl = chrome.runtime.getURL(path);

  // 检查是否已存在
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [offscreenUrl]
  });

  if (existingContexts.length > 0) {
    return;  // 已存在，直接返回
  }

  // 防止并发创建
  if (creating) {
    await creating;
  } else {
    creating = chrome.offscreen.createDocument({
      url: path,
      reasons: ['CLIPBOARD'],
      justification: '需要访问剪贴板以复制文本'
    });
    await creating;
    creating = null;
  }
}
```

### 完整使用示例

```javascript
// service_worker.js

chrome.action.onClicked.addListener(async () => {
  // 1. 确保 Offscreen 文档存在
  await setupOffscreenDocument('offscreen.html');

  // 2. 发送消息到 Offscreen 文档
  const result = await chrome.runtime.sendMessage({
    type: 'COPY_TO_CLIPBOARD',
    target: 'offscreen',
    data: '要复制的文本'
  });

  console.log('复制结果:', result);

  // 3. 可选：任务完成后关闭文档
  // await chrome.offscreen.closeDocument();
});
```

## Offscreen 文档实现

### HTML 文件

```html
<!-- offscreen.html -->
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Offscreen Document</title>
</head>
<body>
  <!-- 用于剪贴板操作的隐藏元素 -->
  <textarea id="clipboard-area"></textarea>
  <script src="offscreen.js"></script>
</body>
</html>
```

### JavaScript 文件

```javascript
// offscreen.js

// 监听来自 Service Worker 的消息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 验证消息目标
  if (message.target !== 'offscreen') {
    return;
  }

  switch (message.type) {
    case 'COPY_TO_CLIPBOARD':
      copyToClipboard(message.data)
        .then(() => sendResponse({ success: true }))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;  // 异步响应

    case 'READ_FROM_CLIPBOARD':
      readFromClipboard()
        .then(text => sendResponse({ success: true, data: text }))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;

    case 'PARSE_HTML':
      const result = parseHTML(message.data);
      sendResponse({ success: true, data: result });
      return false;  // 同步响应
  }
});

// 复制到剪贴板
async function copyToClipboard(text) {
  const textarea = document.getElementById('clipboard-area');
  textarea.value = text;
  textarea.select();
  document.execCommand('copy');

  // 或使用 Clipboard API（需要用户授权）
  // await navigator.clipboard.writeText(text);
}

// 从剪贴板读取
async function readFromClipboard() {
  // 使用 Clipboard API
  return await navigator.clipboard.readText();
}

// 解析 HTML
function parseHTML(htmlString) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(htmlString, 'text/html');
  return {
    title: doc.title,
    bodyText: doc.body.textContent,
    links: Array.from(doc.querySelectorAll('a')).map(a => ({
      href: a.href,
      text: a.textContent
    }))
  };
}
```

## 常见使用场景

### 1. 剪贴板操作

```javascript
// service_worker.js
async function copyText(text) {
  await setupOffscreenDocument('offscreen.html');

  const result = await chrome.runtime.sendMessage({
    type: 'COPY_TO_CLIPBOARD',
    target: 'offscreen',
    data: text
  });

  return result.success;
}

// offscreen.js
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    // 降级方案：使用 execCommand
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    const success = document.execCommand('copy');
    document.body.removeChild(textarea);
    return success;
  }
}
```

### 2. DOM 解析

```javascript
// service_worker.js
async function parseWebPage(htmlContent) {
  await setupOffscreenDocument('offscreen.html');

  const result = await chrome.runtime.sendMessage({
    type: 'PARSE_DOM',
    target: 'offscreen',
    data: htmlContent
  });

  return result.data;
}

// offscreen.js
function parseDOM(html) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html');

  // 提取所需数据
  return {
    title: doc.querySelector('title')?.textContent,
    headings: Array.from(doc.querySelectorAll('h1, h2, h3')).map(h => h.textContent),
    images: Array.from(doc.querySelectorAll('img')).map(img => img.src),
    tables: extractTables(doc)
  };
}

function extractTables(doc) {
  return Array.from(doc.querySelectorAll('table')).map(table => {
    const rows = Array.from(table.querySelectorAll('tr'));
    return rows.map(row => {
      const cells = Array.from(row.querySelectorAll('td, th'));
      return cells.map(cell => cell.textContent.trim());
    });
  });
}
```

### 3. 音频播放

```javascript
// service_worker.js
async function playNotificationSound(soundUrl) {
  await chrome.offscreen.createDocument({
    url: 'audio-player.html',
    reasons: ['AUDIO_PLAYBACK'],
    justification: '播放通知提示音'
  });

  await chrome.runtime.sendMessage({
    type: 'PLAY_AUDIO',
    target: 'offscreen',
    data: { url: soundUrl }
  });
}

// audio-player.js
let audioElement = null;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'PLAY_AUDIO') {
    playAudio(message.data.url)
      .then(() => sendResponse({ success: true }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
});

async function playAudio(url) {
  if (!audioElement) {
    audioElement = new Audio();
  }
  audioElement.src = url;
  await audioElement.play();
}
```

### 4. 获取地理位置

```javascript
// service_worker.js
const OFFSCREEN_DOCUMENT_PATH = 'offscreen.html';

async function getGeolocation() {
  await setupOffscreenDocument(OFFSCREEN_DOCUMENT_PATH);

  const geolocation = await chrome.runtime.sendMessage({
    type: 'GET_GEOLOCATION',
    target: 'offscreen'
  });

  // 获取完毕后关闭文档
  await chrome.offscreen.closeDocument();

  return geolocation;
}

// offscreen.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'GET_GEOLOCATION') {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        sendResponse({
          success: true,
          data: {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy
          }
        });
      },
      (error) => {
        sendResponse({
          success: false,
          error: error.message
        });
      }
    );
    return true;  // 异步响应
  }
});
```

## 错误处理

```javascript
// service_worker.js
async function safeOffscreenOperation(operation) {
  try {
    await setupOffscreenDocument('offscreen.html');
    return await operation();
  } catch (error) {
    console.error('Offscreen operation failed:', error);

    // 尝试关闭并重新创建
    try {
      await chrome.offscreen.closeDocument();
    } catch (e) {
      // 忽略关闭错误
    }

    throw error;
  }
}

// 使用
const result = await safeOffscreenOperation(async () => {
  return await chrome.runtime.sendMessage({
    type: 'SOME_OPERATION',
    target: 'offscreen'
  });
});
```

## 最佳实践

### 1. 单例模式管理

```javascript
class OffscreenManager {
  constructor() {
    this.path = 'offscreen.html';
    this.creating = null;
  }

  async ensure() {
    const exists = await this.hasDocument();
    if (exists) return;

    if (this.creating) {
      await this.creating;
      return;
    }

    this.creating = chrome.offscreen.createDocument({
      url: this.path,
      reasons: ['CLIPBOARD', 'DOM_PARSER'],
      justification: '执行 DOM 相关操作'
    });

    await this.creating;
    this.creating = null;
  }

  async hasDocument() {
    if (!('getContexts' in chrome.runtime)) {
      return false;
    }

    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
      documentUrls: [chrome.runtime.getURL(this.path)]
    });

    return contexts.length > 0;
  }

  async close() {
    const exists = await this.hasDocument();
    if (exists) {
      await chrome.offscreen.closeDocument();
    }
  }

  async sendMessage(type, data) {
    await this.ensure();
    return chrome.runtime.sendMessage({
      type,
      target: 'offscreen',
      data
    });
  }
}

// 使用
const offscreen = new OffscreenManager();
const result = await offscreen.sendMessage('COPY_TEXT', 'Hello World');
```

### 2. 消息路由

```javascript
// offscreen.js
const handlers = {
  COPY_TO_CLIPBOARD: handleCopyToClipboard,
  READ_FROM_CLIPBOARD: handleReadFromClipboard,
  PARSE_HTML: handleParseHTML,
  PLAY_AUDIO: handlePlayAudio
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'offscreen') {
    return false;
  }

  const handler = handlers[message.type];
  if (!handler) {
    sendResponse({ success: false, error: 'Unknown message type' });
    return false;
  }

  // 调用处理器
  const result = handler(message.data, sendResponse);

  // 如果处理器返回 true 或 Promise，保持通道开放
  if (result === true || result instanceof Promise) {
    return true;
  }

  return false;
});
```

## 注意事项

1. **单文档限制**：每个扩展同时只能有一个 Offscreen 文档
2. **显式关闭**：完成任务后应关闭文档以释放资源
3. **并发控制**：使用全局 Promise 防止并发创建
4. **消息目标**：始终验证消息的 `target` 字段
5. **兼容性**：Chrome 116+ 使用 `runtime.getContexts`，之前版本使用 `clients.matchAll`
6. **Reason 匹配**：确保 `reasons` 与实际操作匹配

## 使用场景

- 剪贴板读写操作
- HTML/DOM 解析
- 音视频播放
- WebRTC 通信
- 地理位置获取
- localStorage 访问
- Web Workers 创建
- Blob URL 处理
