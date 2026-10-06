import { createApp, h } from "vue";
import { ElConfigProvider } from "element-plus/es/components/config-provider/index.mjs";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import App from "./App.vue";
import "./style.css";

createApp({
  render: () =>
    h(ElConfigProvider, { locale: zhCn }, { default: () => h(App) }),
}).mount("#app");
