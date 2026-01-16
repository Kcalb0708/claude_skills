---
name: mv3-module-loading
description: Chrome Extension Manifest V3 模块化与懒加载技术。涵盖 ES Modules、动态 import()、Hash 路由、模块生命周期管理等模式。适用于"构建复杂单页扩展应用"的场景。
---

# MV3 模块加载技能

## 概述

Manifest V3 支持原生 ES Modules，无需构建工具即可实现模块化开发。结合动态 `import()` 可实现按需加载，优化扩展性能。

## ES Modules 配置

### manifest.json

```json
{
  "background": {
    "service_worker": "service_worker.js",
    "type": "module"  // 启用 ES Modules
  }
}
```

### 扩展页面

```html
<!-- pages/dashboard/index.html -->
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Dashboard</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div id="app"></div>
  <!-- type="module" 启用 ES Modules -->
  <script type="module" src="router.js"></script>
</body>
</html>
```

## 模块导入导出

### 命名导出

```javascript
// lib/utils.js
export function formatDate(date) {
  return new Date(date).toLocaleDateString();
}

export function formatCurrency(amount) {
  return `¥${amount.toFixed(2)}`;
}

export const CONFIG = {
  apiTimeout: 5000,
  maxRetries: 3
};

// 使用
import { formatDate, formatCurrency, CONFIG } from './lib/utils.js';
```

### 默认导出

```javascript
// lib/api.js
class ApiService {
  constructor() {
    this.baseUrl = 'https://api.example.com';
  }

  async get(path) {
    const response = await fetch(`${this.baseUrl}${path}`);
    return response.json();
  }
}

export default ApiService;

// 使用
import ApiService from './lib/api.js';
const api = new ApiService();
```

### 混合导出

```javascript
// lib/database.js
export const DB_NAME = 'my-extension-db';
export const DB_VERSION = 1;

class DatabaseManager {
  // ...
}

export default DatabaseManager;

// 使用
import DatabaseManager, { DB_NAME, DB_VERSION } from './lib/database.js';
```

## 动态 import() 懒加载

### 基础用法

```javascript
// 按需加载模块
async function loadModule(moduleName) {
  const module = await import(`./modules/${moduleName}.js`);
  return module;
}

// 条件加载
if (userNeedsAdvancedFeature) {
  const { AdvancedFeature } = await import('./lib/advanced-feature.js');
  const feature = new AdvancedFeature();
}
```

### 路由懒加载

```javascript
// router.js - Hash 路由 + 懒加载

const routes = {
  '/dashboard': () => import('./sections/dashboard.js'),
  '/settlement': () => import('./sections/settlement.js'),
  '/sales': () => import('./sections/sales.js'),
  '/ops': () => import('./sections/ops.js')
};

let currentModule = null;

async function navigate(path) {
  const loader = routes[path];
  if (!loader) {
    console.error('Route not found:', path);
    return;
  }

  // 卸载当前模块
  if (currentModule?.unmount) {
    await currentModule.unmount();
  }

  // 显示加载状态
  showLoading();

  try {
    // 动态加载模块
    const module = await loader();

    // 渲染新模块
    const container = document.getElementById('content');
    currentModule = await module.render(container);

  } catch (error) {
    console.error('Failed to load module:', error);
    showError(error);
  } finally {
    hideLoading();
  }
}

// 监听 hash 变化
window.addEventListener('hashchange', () => {
  const path = location.hash.slice(1) || '/dashboard';
  navigate(path);
});

// 初始导航
const initialPath = location.hash.slice(1) || '/dashboard';
navigate(initialPath);
```

### 模块结构

```javascript
// sections/dashboard.js - 功能模块模板

// 导入依赖
import ApiService from '../lib/api.js';
import { formatCurrency } from '../lib/utils.js';

// 模块私有状态
let chartInstance = null;
let updateInterval = null;

/**
 * 渲染模块
 * @param {HTMLElement} container - 容器元素
 * @returns {Object} 包含 unmount 方法的对象
 */
export async function render(container) {
  console.log('[Dashboard] Rendering...');

  // 渲染 HTML
  container.innerHTML = `
    <div class="dashboard">
      <h1>数据看板</h1>
      <div class="stats-grid">
        <div class="stat-card" id="sales-card">加载中...</div>
        <div class="stat-card" id="profit-card">加载中...</div>
      </div>
      <div id="chart-container" style="height: 400px;"></div>
    </div>
  `;

  // 初始化数据
  await loadData();

  // 初始化图表
  initChart();

  // 设置定时更新
  updateInterval = setInterval(loadData, 60000);

  // 返回模块控制对象
  return {
    unmount
  };
}

async function loadData() {
  try {
    const api = new ApiService();
    const data = await api.get('/dashboard/stats');
    updateUI(data);
  } catch (error) {
    console.error('Failed to load data:', error);
  }
}

function updateUI(data) {
  document.getElementById('sales-card').textContent =
    `销售额: ${formatCurrency(data.sales)}`;
  document.getElementById('profit-card').textContent =
    `利润: ${formatCurrency(data.profit)}`;
}

function initChart() {
  const container = document.getElementById('chart-container');
  // 假设使用 ECharts
  if (window.echarts) {
    chartInstance = echarts.init(container);
    chartInstance.setOption({
      // 图表配置...
    });
  }
}

/**
 * 卸载模块 - 清理资源
 */
function unmount() {
  console.log('[Dashboard] Unmounting...');

  // 清除定时器
  if (updateInterval) {
    clearInterval(updateInterval);
    updateInterval = null;
  }

  // 销毁图表
  if (chartInstance) {
    chartInstance.dispose();
    chartInstance = null;
  }

  // 移除事件监听器
  // ...
}
```

## 模块生命周期管理

### 生命周期钩子

```javascript
// lib/module-manager.js

class ModuleManager {
  constructor() {
    this.currentModule = null;
    this.moduleCache = new Map();
  }

  async load(path, loader) {
    // 检查缓存
    if (!this.moduleCache.has(path)) {
      const module = await loader();
      this.moduleCache.set(path, module);
    }
    return this.moduleCache.get(path);
  }

  async switch(path, loader, container) {
    // 1. 调用当前模块的 beforeUnmount 钩子
    if (this.currentModule?.beforeUnmount) {
      const canLeave = await this.currentModule.beforeUnmount();
      if (!canLeave) {
        console.log('Navigation cancelled by module');
        return false;
      }
    }

    // 2. 卸载当前模块
    if (this.currentModule?.unmount) {
      await this.currentModule.unmount();
    }

    // 3. 加载新模块
    const module = await this.load(path, loader);

    // 4. 调用 beforeMount 钩子
    if (module.beforeMount) {
      await module.beforeMount();
    }

    // 5. 渲染模块
    this.currentModule = await module.render(container);

    // 6. 调用 mounted 钩子
    if (this.currentModule.mounted) {
      await this.currentModule.mounted();
    }

    return true;
  }

  clearCache(path) {
    if (path) {
      this.moduleCache.delete(path);
    } else {
      this.moduleCache.clear();
    }
  }
}

export default new ModuleManager();
```

### 使用生命周期

```javascript
// sections/settlement.js

let formData = null;
let isDirty = false;

export async function render(container) {
  container.innerHTML = `
    <form id="settlement-form">
      <input type="text" id="amount" />
      <button type="submit">提交</button>
    </form>
  `;

  const form = document.getElementById('settlement-form');
  form.addEventListener('input', () => { isDirty = true; });
  form.addEventListener('submit', handleSubmit);

  return {
    beforeUnmount,
    unmount
  };
}

// 离开前确认
async function beforeUnmount() {
  if (isDirty) {
    const confirmed = await window.TemuBI.confirm(
      '未保存的更改',
      '您有未保存的更改，确定要离开吗？'
    );
    return confirmed;
  }
  return true;
}

function unmount() {
  formData = null;
  isDirty = false;
}
```

## 全局状态管理

### 简单状态对象

```javascript
// router.js - 全局状态

window.TemuBI = {
  // 数据缓存
  data: {
    sales: null,
    traffic: null,
    settlement: null
  },

  // 服务实例（懒加载）
  _api: null,
  get api() {
    if (!this._api) {
      // 动态导入
      import('./lib/api.js').then(m => {
        this._api = new m.default();
      });
    }
    return this._api;
  },

  // 事件系统
  events: new EventTarget(),

  // 通知方法
  notify(message, type = 'info') {
    this.events.dispatchEvent(new CustomEvent('notification', {
      detail: { message, type }
    }));
  },

  // 确认对话框
  confirm(title, message) {
    return new Promise(resolve => {
      // 实现确认对话框逻辑
      const result = window.confirm(`${title}\n\n${message}`);
      resolve(result);
    });
  }
};

// 模块中使用
// import 不需要，直接访问 window.TemuBI
window.TemuBI.events.addEventListener('data-updated', (e) => {
  console.log('Data updated:', e.detail);
});
```

### 响应式状态

```javascript
// lib/store.js - 简单响应式状态

class Store {
  constructor(initialState = {}) {
    this._state = initialState;
    this._listeners = new Map();
  }

  get(key) {
    return this._state[key];
  }

  set(key, value) {
    const oldValue = this._state[key];
    this._state[key] = value;

    // 通知监听器
    if (this._listeners.has(key)) {
      this._listeners.get(key).forEach(callback => {
        callback(value, oldValue);
      });
    }
  }

  subscribe(key, callback) {
    if (!this._listeners.has(key)) {
      this._listeners.set(key, new Set());
    }
    this._listeners.get(key).add(callback);

    // 返回取消订阅函数
    return () => {
      this._listeners.get(key).delete(callback);
    };
  }
}

export default new Store({
  user: null,
  theme: 'light',
  syncStatus: 'idle'
});
```

## 模块间通信

### 事件总线

```javascript
// lib/event-bus.js

class EventBus extends EventTarget {
  emit(event, data) {
    this.dispatchEvent(new CustomEvent(event, { detail: data }));
  }

  on(event, callback) {
    const handler = (e) => callback(e.detail);
    this.addEventListener(event, handler);
    return () => this.removeEventListener(event, handler);
  }

  once(event, callback) {
    const handler = (e) => {
      callback(e.detail);
      this.removeEventListener(event, handler);
    };
    this.addEventListener(event, handler);
  }
}

export default new EventBus();

// 使用
import eventBus from '../lib/event-bus.js';

// 发送事件
eventBus.emit('data-loaded', { items: [...] });

// 监听事件
const unsubscribe = eventBus.on('data-loaded', (data) => {
  console.log('Received:', data);
});

// 取消监听
unsubscribe();
```

## 资源预加载

```javascript
// 预加载关键模块
function preloadModules() {
  // 使用 link rel="modulepreload"（如果支持）
  const criticalModules = [
    './lib/api.js',
    './lib/utils.js',
    './sections/dashboard.js'
  ];

  criticalModules.forEach(path => {
    const link = document.createElement('link');
    link.rel = 'modulepreload';
    link.href = path;
    document.head.appendChild(link);
  });
}

// 或使用动态 import 预加载
async function preloadInBackground() {
  // 后台预加载其他模块
  setTimeout(async () => {
    await Promise.all([
      import('./sections/settlement.js'),
      import('./sections/sales.js'),
      import('./sections/ops.js')
    ]);
    console.log('All modules preloaded');
  }, 2000);
}
```

## 错误处理

```javascript
// 模块加载错误处理
async function safeImport(loader, fallback = null) {
  try {
    return await loader();
  } catch (error) {
    console.error('Module load error:', error);

    // 显示用户友好的错误信息
    showError(`模块加载失败: ${error.message}`);

    // 返回降级模块
    return fallback || {
      render: (container) => {
        container.innerHTML = '<div class="error">模块加载失败</div>';
        return { unmount: () => {} };
      }
    };
  }
}

// 使用
const module = await safeImport(
  () => import('./sections/dashboard.js'),
  { render: () => ({ unmount: () => {} }) }
);
```

## 目录结构建议

```
extension/
├── manifest.json
├── service_worker.js          # Service Worker (type: module)
├── lib/                       # 核心库
│   ├── api.js                 # API 服务
│   ├── utils.js               # 工具函数
│   ├── store.js               # 状态管理
│   └── event-bus.js           # 事件总线
├── pages/
│   └── dashboard/
│       ├── index.html         # 入口 HTML
│       ├── router.js          # 路由控制
│       ├── styles.css         # 样式
│       └── sections/          # 功能模块
│           ├── dashboard.js
│           ├── settlement.js
│           ├── sales.js
│           └── ops.js
└── content/
    └── bridge.js              # Content Script
```

## 注意事项

1. **文件扩展名**：import 路径必须包含 `.js` 扩展名
2. **相对路径**：使用 `./` 或 `../` 开头的相对路径
3. **循环依赖**：避免模块间循环依赖
4. **内存管理**：模块卸载时清理所有资源
5. **错误边界**：每个模块应有独立的错误处理

## 使用场景

- 复杂单页扩展应用
- 按需加载功能模块
- 模块化代码组织
- 路由驱动的页面切换
- 资源优化和性能提升
