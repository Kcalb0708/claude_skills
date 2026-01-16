---
name: mv3-storage
description: Chrome Extension Manifest V3 数据存储技术。涵盖 IndexedDB、chrome.storage、缓存策略、数据隔离等模式。适用于"需要本地持久化数据"的扩展开发场景。
---

# MV3 数据存储技能

## 概述

Manifest V3 中 Service Worker 无法使用 localStorage，需要使用 IndexedDB 或 chrome.storage 进行数据持久化。

## 存储方案对比

| 特性 | localStorage | chrome.storage | IndexedDB |
|------|-------------|----------------|-----------|
| Service Worker | ❌ | ✅ | ✅ |
| Content Script | ✅ (页面域) | ✅ | ✅ |
| Extension Page | ✅ | ✅ | ✅ |
| 容量限制 | 5MB | 见下表 | 无限制 |
| 数据类型 | 字符串 | JSON 对象 | 任意类型 |
| 同步支持 | ❌ | ✅ (sync) | ❌ |
| 查询能力 | ❌ | ❌ | ✅ (索引) |

## chrome.storage 存储区域（官方规范）

### 四种存储区域

| 区域 | 说明 | 容量限制 | Content Script 默认访问 |
|------|------|---------|----------------------|
| **local** | 本地持久存储，扩展卸载时删除 | 10MB（可用 `unlimitedStorage` 增加） | ✅ |
| **sync** | 跨设备同步，登录 Chrome 后同步 | 100KB 总量，8KB/项，512 项上限 | ✅ |
| **session** | 内存存储，扩展卸载/更新/浏览器重启时清除 | 10MB | ❌ |
| **managed** | 企业策略管理，只读 | 由管理员定义 | ✅ |

### sync 存储写入限制

| 限制 | 值 |
|------|-----|
| MAX_ITEMS | 512 项 |
| MAX_WRITE_OPERATIONS_PER_HOUR | 1800 次/小时 |
| MAX_WRITE_OPERATIONS_PER_MINUTE | 120 次/分钟 |
| QUOTA_BYTES | 102400 (100KB) |
| QUOTA_BYTES_PER_ITEM | 8192 (8KB) |

### session 存储（推荐 Service Worker 使用）

```javascript
// session 存储在内存中，性能最佳
// 扩展卸载、更新或浏览器重启时清除

// 保存会话数据
await chrome.storage.session.set({
  tempData: { ... },
  authToken: 'xxx'
});

// 读取会话数据
const { tempData } = await chrome.storage.session.get('tempData');

// ✅ 推荐：Service Worker 临时状态使用 session
// 避免频繁写入 local 存储
```

### setAccessLevel - 控制 Content Script 访问

```javascript
// 允许 Content Script 访问 session 存储（默认不允许）
await chrome.storage.session.setAccessLevel({
  accessLevel: 'TRUSTED_AND_UNTRUSTED_CONTEXTS'
});

// 限制 Content Script 访问 local 存储
await chrome.storage.local.setAccessLevel({
  accessLevel: 'TRUSTED_CONTEXTS'  // 仅扩展页面可访问
});

// accessLevel 选项：
// - 'TRUSTED_CONTEXTS': 仅扩展页面（Service Worker、popup、options 等）
// - 'TRUSTED_AND_UNTRUSTED_CONTEXTS': 扩展页面 + Content Script
```

## chrome.storage

### manifest.json 配置

```json
{
  "permissions": ["storage"]
}
```

### 基础用法

```javascript
// 保存数据
await chrome.storage.local.set({
  user: { name: 'John', id: 123 },
  settings: { theme: 'dark' }
});

// 读取数据
const result = await chrome.storage.local.get(['user', 'settings']);
console.log(result.user);     // { name: 'John', id: 123 }
console.log(result.settings); // { theme: 'dark' }

// 读取所有数据
const all = await chrome.storage.local.get(null);

// 删除数据
await chrome.storage.local.remove('user');

// 清空所有数据
await chrome.storage.local.clear();
```

### 监听变化

```javascript
chrome.storage.onChanged.addListener((changes, namespace) => {
  for (const [key, { oldValue, newValue }] of Object.entries(changes)) {
    console.log(
      `Storage key "${key}" in ${namespace} changed:`,
      `${JSON.stringify(oldValue)} → ${JSON.stringify(newValue)}`
    );
  }
});
```

### 封装工具类

```javascript
// lib/storage.js

class Storage {
  constructor(namespace = 'local') {
    this.store = chrome.storage[namespace];
  }

  async get(key, defaultValue = null) {
    const result = await this.store.get(key);
    return result[key] ?? defaultValue;
  }

  async set(key, value) {
    await this.store.set({ [key]: value });
  }

  async remove(key) {
    await this.store.remove(key);
  }

  async clear() {
    await this.store.clear();
  }

  // 带过期时间的存储
  async setWithExpiry(key, value, ttlMinutes) {
    const item = {
      value,
      expiry: Date.now() + ttlMinutes * 60 * 1000
    };
    await this.set(key, item);
  }

  async getWithExpiry(key) {
    const item = await this.get(key);
    if (!item) return null;

    if (Date.now() > item.expiry) {
      await this.remove(key);
      return null;
    }
    return item.value;
  }
}

export const localStorage = new Storage('local');
export const syncStorage = new Storage('sync');
```

## IndexedDB

### 数据库管理器

```javascript
// lib/idb.js

const DB_NAME = 'temu-bi-db';
const DB_VERSION = 1;

class DatabaseManager {
  constructor() {
    this.db = null;
  }

  async init() {
    if (this.db) return this.db;

    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onerror = () => reject(request.error);

      request.onsuccess = () => {
        this.db = request.result;
        resolve(this.db);
      };

      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        this.createStores(db);
      };
    });
  }

  createStores(db) {
    // 销售数据表
    if (!db.objectStoreNames.contains('sales')) {
      const store = db.createObjectStore('sales', {
        keyPath: ['mallId', 'skcId']
      });
      store.createIndex('mallId', 'mallId', { unique: false });
      store.createIndex('updatedAt', 'updatedAt', { unique: false });
    }

    // 流量数据表
    if (!db.objectStoreNames.contains('traffic')) {
      const store = db.createObjectStore('traffic', {
        keyPath: ['mallId', 'goodsId', 'region']
      });
      store.createIndex('mallId', 'mallId', { unique: false });
    }

    // 通用缓存表
    if (!db.objectStoreNames.contains('cache')) {
      const store = db.createObjectStore('cache', { keyPath: 'key' });
      store.createIndex('expiry', 'expiry', { unique: false });
    }

    // ERP采购价缓存
    if (!db.objectStoreNames.contains('erp_prices')) {
      const store = db.createObjectStore('erp_prices', { keyPath: 'id' });
      store.createIndex('updatedAt', 'updatedAt', { unique: false });
    }
  }

  // ============================================
  // 通用 CRUD 操作
  // ============================================

  async add(storeName, data) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.add(data);

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async put(storeName, data) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.put(data);

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async get(storeName, key) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const store = tx.objectStore(storeName);
      const request = store.get(key);

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async getAll(storeName, indexName, query) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const store = tx.objectStore(storeName);
      const target = indexName ? store.index(indexName) : store;
      const request = target.getAll(query);

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async delete(storeName, key) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.delete(key);

      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }

  async clear(storeName) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.clear();

      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }

  // ============================================
  // 批量操作
  // ============================================

  async putBatch(storeName, items) {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);

      let completed = 0;
      const errors = [];

      items.forEach(item => {
        const request = store.put(item);
        request.onsuccess = () => {
          completed++;
          if (completed === items.length) {
            resolve({ success: items.length - errors.length, errors });
          }
        };
        request.onerror = () => {
          errors.push(request.error);
          completed++;
          if (completed === items.length) {
            resolve({ success: items.length - errors.length, errors });
          }
        };
      });
    });
  }

  // ============================================
  // 缓存管理
  // ============================================

  async setCache(key, value, ttlMinutes = 30) {
    await this.put('cache', {
      key,
      value,
      expiry: Date.now() + ttlMinutes * 60 * 1000,
      createdAt: Date.now()
    });
  }

  async getCache(key) {
    const item = await this.get('cache', key);
    if (!item) return null;

    if (Date.now() > item.expiry) {
      await this.delete('cache', key);
      return null;
    }
    return item.value;
  }

  async clearExpiredCache() {
    const db = await this.init();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('cache', 'readwrite');
      const store = tx.objectStore('cache');
      const index = store.index('expiry');
      const now = Date.now();

      const range = IDBKeyRange.upperBound(now);
      const request = index.openCursor(range);

      let deleted = 0;
      request.onsuccess = (event) => {
        const cursor = event.target.result;
        if (cursor) {
          cursor.delete();
          deleted++;
          cursor.continue();
        } else {
          resolve(deleted);
        }
      };
      request.onerror = () => reject(request.error);
    });
  }
}

export default new DatabaseManager();
```

### 使用示例

```javascript
import db from './lib/idb.js';

// 初始化
await db.init();

// 保存销售数据
await db.put('sales', {
  mallId: '91288',
  skcId: 'SKU001',
  goodsName: '商品A',
  saleVolume: 100,
  revenue: 5000,
  updatedAt: Date.now()
});

// 批量保存
await db.putBatch('sales', salesItems);

// 查询店铺所有销售数据
const salesData = await db.getAll('sales', 'mallId', '91288');

// 使用缓存
await db.setCache('api-result', responseData, 5);  // 5分钟过期
const cached = await db.getCache('api-result');

// 清理过期缓存
const deletedCount = await db.clearExpiredCache();
```

## 数据隔离策略

### 按店铺隔离

```javascript
// 使用复合主键实现数据隔离
class MallDataManager {
  constructor(mallId) {
    this.mallId = mallId;
  }

  async getSalesData() {
    return db.getAll('sales', 'mallId', this.mallId);
  }

  async saveSalesData(items) {
    const dataWithMallId = items.map(item => ({
      ...item,
      mallId: this.mallId,
      updatedAt: Date.now()
    }));
    return db.putBatch('sales', dataWithMallId);
  }

  async clearMallData() {
    const items = await this.getSalesData();
    for (const item of items) {
      await db.delete('sales', [this.mallId, item.skcId]);
    }
  }
}

// 使用
const mallManager = new MallDataManager('91288');
const data = await mallManager.getSalesData();
```

## 缓存策略

### 1. 时间过期缓存

```javascript
class TimedCache {
  constructor(defaultTTL = 5 * 60 * 1000) {  // 默认5分钟
    this.defaultTTL = defaultTTL;
  }

  async get(key) {
    const cached = await db.getCache(key);
    return cached;
  }

  async set(key, value, ttl = this.defaultTTL) {
    await db.setCache(key, value, ttl / 60000);
  }

  async getOrFetch(key, fetcher, ttl = this.defaultTTL) {
    const cached = await this.get(key);
    if (cached !== null) {
      console.log('Cache hit:', key);
      return cached;
    }

    console.log('Cache miss:', key);
    const value = await fetcher();
    await this.set(key, value, ttl);
    return value;
  }
}

// 使用
const cache = new TimedCache();

const data = await cache.getOrFetch(
  'sales-data-91288',
  () => api.getSalesData('91288'),
  30 * 60 * 1000  // 30分钟
);
```

### 2. 版本化缓存

```javascript
class VersionedCache {
  async get(key, version) {
    const item = await db.get('cache', key);
    if (!item) return null;
    if (item.version !== version) return null;
    return item.value;
  }

  async set(key, value, version) {
    await db.put('cache', {
      key,
      value,
      version,
      updatedAt: Date.now()
    });
  }
}

// 使用（数据格式变更时更新版本号）
const CACHE_VERSION = 'v2';
const cache = new VersionedCache();

await cache.set('config', configData, CACHE_VERSION);
const config = await cache.get('config', CACHE_VERSION);
```

### 3. 当日缓存

```javascript
function getTodayKey(baseKey) {
  const today = new Date().toISOString().split('T')[0];
  return `${baseKey}_${today}`;
}

async function getDailyData(key, fetcher) {
  const dailyKey = getTodayKey(key);
  const cached = await db.getCache(dailyKey);

  if (cached) return cached;

  const data = await fetcher();
  await db.setCache(dailyKey, data, 24 * 60);  // 24小时
  return data;
}
```

## 数据迁移

```javascript
// 版本升级时迁移数据
const DB_VERSION = 2;  // 升级版本号

request.onupgradeneeded = (event) => {
  const db = event.target.result;
  const oldVersion = event.oldVersion;

  if (oldVersion < 1) {
    // 初始化表结构
    createStores(db);
  }

  if (oldVersion < 2) {
    // 版本2新增字段
    const tx = event.target.transaction;
    const store = tx.objectStore('sales');

    // 添加新索引
    if (!store.indexNames.contains('category')) {
      store.createIndex('category', 'category', { unique: false });
    }
  }
};
```

## 数据导出导入

```javascript
// 导出数据
async function exportData() {
  const data = {
    sales: await db.getAll('sales'),
    traffic: await db.getAll('traffic'),
    cache: await db.getAll('cache'),
    exportedAt: Date.now()
  };

  const blob = new Blob([JSON.stringify(data)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  const a = document.createElement('a');
  a.href = url;
  a.download = `backup-${new Date().toISOString().split('T')[0]}.json`;
  a.click();

  URL.revokeObjectURL(url);
}

// 导入数据
async function importData(file) {
  const text = await file.text();
  const data = JSON.parse(text);

  if (data.sales) {
    await db.putBatch('sales', data.sales);
  }
  if (data.traffic) {
    await db.putBatch('traffic', data.traffic);
  }

  console.log('Data imported successfully');
}
```

## 存储空间管理

```javascript
// 估算使用量
async function getStorageEstimate() {
  if (navigator.storage && navigator.storage.estimate) {
    const estimate = await navigator.storage.estimate();
    return {
      usage: estimate.usage,
      quota: estimate.quota,
      percent: ((estimate.usage / estimate.quota) * 100).toFixed(2) + '%'
    };
  }
  return null;
}

// 清理旧数据
async function cleanupOldData(daysToKeep = 30) {
  const cutoff = Date.now() - daysToKeep * 24 * 60 * 60 * 1000;

  // 清理过期销售数据
  const salesData = await db.getAll('sales');
  for (const item of salesData) {
    if (item.updatedAt < cutoff) {
      await db.delete('sales', [item.mallId, item.skcId]);
    }
  }

  // 清理过期缓存
  await db.clearExpiredCache();
}
```

## 错误处理

```javascript
class SafeDatabase {
  async safeOperation(operation, fallback = null) {
    try {
      return await operation();
    } catch (error) {
      console.error('Database error:', error);

      // 处理配额超限
      if (error.name === 'QuotaExceededError') {
        await this.handleQuotaExceeded();
        // 重试
        return await operation();
      }

      return fallback;
    }
  }

  async handleQuotaExceeded() {
    console.warn('Storage quota exceeded, cleaning up...');
    await db.clearExpiredCache();
    await cleanupOldData(7);  // 只保留7天数据
  }
}
```

## 调试技巧

```javascript
// 1. 在 DevTools Application 面板查看 IndexedDB 数据

// 2. 导出所有数据
window.debugExportDB = async () => {
  const data = {};
  for (const storeName of db.db.objectStoreNames) {
    data[storeName] = await db.getAll(storeName);
  }
  console.log(JSON.stringify(data, null, 2));
};

// 3. 清空数据库
window.debugClearDB = async () => {
  indexedDB.deleteDatabase(DB_NAME);
  location.reload();
};
```

## 注意事项

1. **Service Worker 限制**：无法使用 localStorage
2. **异步操作**：IndexedDB 所有操作都是异步的
3. **事务**：写操作需要在事务中完成
4. **版本管理**：数据结构变更需要升级数据库版本
5. **容量限制**：chrome.storage.local 限制 5MB

## 使用场景

- 缓存 API 响应数据
- 持久化用户设置
- 离线数据存储
- 跨会话数据保留
- 大量结构化数据存储

---

## Plasmo Storage API

Plasmo 提供 `@plasmohq/storage` 包，支持 React Hook 响应式存储和跨组件状态同步。

### 安装

```bash
pnpm add @plasmohq/storage
```

### 基础用法

```typescript
import { Storage } from "@plasmohq/storage"

// 创建存储实例（默认使用 chrome.storage.local）
const storage = new Storage()

// 或指定存储区域
const syncStorage = new Storage({ area: "sync" })
const sessionStorage = new Storage({ area: "session" })

// 基础操作
await storage.set("key", { foo: "bar" })
const value = await storage.get("key")
await storage.remove("key")
```

### useStorage Hook（React）

```tsx
import { useStorage } from "@plasmohq/storage/hook"

function SettingsPanel() {
  // 基础用法
  const [theme, setTheme] = useStorage("theme", "light")

  // 带初始化函数（仅首次加载时执行）
  const [count, setCount] = useStorage<number>("open-count", (stored) =>
    typeof stored === "undefined" ? 0 : stored + 1
  )

  // 指定存储区域
  const [syncData] = useStorage({
    key: "sync-data",
    area: "sync"
  })

  return (
    <div>
      <select value={theme} onChange={(e) => setTheme(e.target.value)}>
        <option value="light">浅色</option>
        <option value="dark">深色</option>
      </select>
      <p>打开次数: {count}</p>
    </div>
  )
}
```

### 跨组件状态同步

useStorage 自动同步所有使用相同 key 的组件。

```tsx
// popup.tsx
function PopupCounter() {
  const [count, setCount] = useStorage("counter", 0)
  return <button onClick={() => setCount(count + 1)}>+1 ({count})</button>
}

// options.tsx - 自动同步更新
function OptionsCounter() {
  const [count] = useStorage("counter", 0)
  return <p>当前计数: {count}</p>
}
```

### watch 监听变化

```typescript
import { Storage } from "@plasmohq/storage"

const storage = new Storage()

// 监听单个 key
storage.watch({
  "user-settings": (change) => {
    console.log("旧值:", change.oldValue)
    console.log("新值:", change.newValue)
  }
})

// 监听多个 key
storage.watch({
  theme: (c) => applyTheme(c.newValue),
  language: (c) => applyLanguage(c.newValue)
})
```

### SecureStorage（加密存储）

```typescript
import { SecureStorage } from "@plasmohq/storage/secure"

const secureStorage = new SecureStorage()

// 需要先设置密码
await secureStorage.setPassword("your-secret-password")

// 加密存储敏感数据
await secureStorage.set("api-key", "sk-xxx...")
const apiKey = await secureStorage.get("api-key")
```

### useStorage 完整配置

```typescript
const [value, setValue, { remove, isLoading }] = useStorage({
  key: "my-key",
  area: "local",      // local | sync | session
  instance: storage   // 可选：自定义 Storage 实例
}, initialValue)

// 删除数据
await remove()

// 检查加载状态
if (isLoading) return <Loading />
```

### Plasmo vs 原生存储对比

| 特性 | 原生 chrome.storage | Plasmo Storage |
|------|---------------------|----------------|
| React 集成 | 需手动实现 | useStorage Hook |
| 状态同步 | 需 onChanged 监听 | 自动同步 |
| 类型安全 | 需手动定义 | 泛型支持 |
| 变化监听 | onChanged.addListener | storage.watch() |
| 加密存储 | 需自行实现 | SecureStorage |

### 在 Content Script 中使用

```tsx
// contents/panel.tsx
import { useStorage } from "@plasmohq/storage/hook"

export default function ContentPanel() {
  // Content Script 中同样可用
  const [settings] = useStorage("settings", { enabled: true })

  if (!settings.enabled) return null

  return <div>面板内容</div>
}
```

### 持久化复杂对象

```typescript
interface UserProfile {
  name: string
  avatar: string
  preferences: {
    theme: string
    notifications: boolean
  }
}

const [profile, setProfile] = useStorage<UserProfile>("profile", {
  name: "",
  avatar: "",
  preferences: { theme: "light", notifications: true }
})

// 更新嵌套属性
setProfile({
  ...profile,
  preferences: { ...profile.preferences, theme: "dark" }
})
```

### 注意事项

1. **区域选择**：`session` 区域不会持久化，重启后丢失
2. **容量限制**：`sync` 区域有 100KB 限制，大数据用 `local`
3. **初始化函数**：仅在值为 `undefined` 时执行
4. **类型安全**：建议使用泛型定义数据类型
5. **SecureStorage**：密码丢失则数据无法恢复
