# Agent-user

基于 Vue 3 + TypeScript 的 AI 智能助手前端应用，支持流式对话、打字机效果和断线重连。

## 功能特性

- 💬 **智能对话**：集成 AI 大模型，支持多轮对话
- 🌊 **流式输出**：Server-Sent Events (SSE) 实时流式响应
- ⌨️ **打字机效果**：平滑的文本打字机动画
- 🔄 **断线重连**：自动检测网络断开并重连，支持断点续传
- 📱 **移动端优先**：响应式设计，完美适配移动设备
- 🎨 **UI 组件**：基于 Vant 4 的移动端组件库
- 🛒 **商品展示**：商品列表、详情展示

## 技术栈

- **框架**：Vue 3 (Composition API + `<script setup>`)
- **语言**：TypeScript
- **构建工具**：Vite
- **状态管理**：Pinia
- **路由**：Vue Router (HTML5 History 模式)
- **UI 组件**：Vant 4
- **样式**：Less + postcss-pxtorem
- **HTTP 客户端**：axios + @microsoft/fetch-event-source
- **响应式**：amfe-flexible

## 目录结构

```
Agent-user/
├── src/
│   ├── api/                # API 请求层
│   │   ├── client.ts              # axios 客户端
│   │   ├── fetchEventSourceRequest.ts  # SSE 流式请求
│   │   ├── conversation.ts        # 对话接口
│   │   ├── goods.ts               # 商品接口
│   │   └── upload.ts              # 上传接口
│   ├── views/              # 页面组件
│   │   ├── Home/
│   │   │   ├── Home.vue           # 主页面
│   │   │   └── components/        # 子组件
│   │   │       ├── ChatPane.vue           # 聊天面板
│   │   │       ├── ChatInputBar.vue       # 输入框
│   │   │       ├── ChatMessageItem.vue    # 消息项
│   │   │       ├── ChatHistory.vue        # 历史记录
│   │   │       ├── ConversationSidebar.vue # 对话侧边栏
│   │   │       ├── SmartSuggestions.vue   # 智能建议
│   │   │       ├── CityWeather.vue        # 城市天气
│   │   │       ├── TrainTicket.vue        # 火车票
│   │   │       └── ProductShowcase.vue    # 商品展示
│   │   └── Goods/
│   │       └── GoodsDetail.vue    # 商品详情
│   ├── store/              # Pinia 状态管理
│   │   └── index.ts        # 聊天状态管理
│   ├── router/             # Vue Router 配置
│   │   └── index.ts
│   ├── types/              # TypeScript 类型定义
│   │   └── index.d.ts
│   ├── utils/              # 工具函数
│   │   ├── typewriter.ts   # 打字机效果
│   │   └── perf.ts         # 性能追踪
│   └── assets/             # 静态资源
│       ├── images/
│       ├── fonts/
│       └── icons/
├── public/                 # 公共静态资源
├── index.html
├── vite.config.ts          # Vite 配置
├── tsconfig.json           # TypeScript 配置
├── package.json
└── README.md
```

## 快速开始

### 环境要求

- Node.js >= 16.0.0
- npm >= 8.0.0

### 安装依赖

```bash
cd Agent-user
npm install
```

### 开发模式

```bash
npm run dev
```

应用启动后访问：http://localhost:8080

### 构建生产版本

```bash
npm run build
```

### 预览生产构建

```bash
npm run preview
```
