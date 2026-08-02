import { createApp } from "vue";

import App from "./App.vue";
import router from "./router/index";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "vue-virtual-scroller/dist/vue-virtual-scroller.css";
// Mock.js 已禁用：会拦截 /api 请求返回假数据，无法连接真实后端
// import "@/api/mock";

const pinia = createPinia();
const app = createApp(App);
app.use(pinia);
app.use(router);
app.use(ElementPlus);
app.mount("#app");
