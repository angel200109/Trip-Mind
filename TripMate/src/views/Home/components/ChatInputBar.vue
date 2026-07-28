<template>
  <div class="chat-input-bar">
    <div class="input-bar">
      <input
        v-model="inputContent"
        type="text"
        class="input-text"
        placeholder="有问题，尽管问"
        @keydown.enter="handleSend"
      />
      <el-icon
        class="send-icon"
        :class="{ 'has-content': inputContent && !store.prohibit }"
        @click="handleSend"
      >
        <Top />
      </el-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Top } from "@element-plus/icons-vue";
import { chatbotMessage } from "@/store/index";
import { uploadFileApi } from "@/api/upload";
import type { ImageContent, TextContent } from "@/types";

const fileList = ref([
  {
    url: "",
  },
]);

const store = chatbotMessage();
const router = useRouter();
const fileInput = ref<HTMLInputElement | null>(null);
const showImage = ref(false);
const inputContent = computed({
  get: () => store.draftInput,
  set: (value: string) => {
    store.draftInput = value;
  },
});

function beforeDelete() {
  fileList.value[0].url = "";
  showImage.value = false;
}

async function navigateToCurrentConversation() {
  if (!store.currentConversationId) return;
  await router.replace(`/chat/${store.currentConversationId}`);
}

async function handleQueryTrainTicket() {
  await store.ensureConversation("帮我查询火车票");
  await navigateToCurrentConversation();
  await store.sendMessage("帮我查询火车票");
}

async function handleQueryWeather() {
  await store.ensureConversation("帮我查询天气");
  await navigateToCurrentConversation();
  await store.sendMessage("帮我查询天气");
}

async function handleSend() {
  if (inputContent.value != "" && fileList.value[0].url) {
    const currentInput = inputContent.value;
    const sendImageAndText: Array<TextContent | ImageContent> = [
      { type: "text", text: currentInput || "" },
      {
        type: "image_url",
        image_url: {
          url: fileList.value[0]?.url || "",
        },
      },
    ];
    await store.ensureConversation(sendImageAndText);
    await navigateToCurrentConversation();
    store.draftInput = "";
    fileList.value[0].url = "";
    showImage.value = false;
    await store.sendMessage(sendImageAndText);
  } else if (inputContent.value != "") {
    const currentInput = inputContent.value;
    await store.ensureConversation(currentInput);
    await navigateToCurrentConversation();
    store.draftInput = "";
    await store.sendMessage(currentInput);
  }
}

function handleSelectFile() {
  fileInput.value?.click();
}

async function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;

  if (!file.type.startsWith("image/")) {
    ElMessage.warning("请上传正确的图片");
    target.value = "";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  const fileUrl = await uploadFileApi(file);
  fileList.value[0].url = "http://" + fileUrl.data;
  showImage.value = true;
}
</script>

<style scoped lang="less">
.chat-input-bar {
  display: flex;
  justify-content: center;
  align-items: center;
  background-color: #fff;
  padding: 16px 24px;
  border-top: 1px solid #f0f0f0;
}

.input-bar {
  width: 100%;
  max-width: 768px;
  display: flex;
  align-items: center;
  border: 1px solid #d9d9d9;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  background-color: #fff;
  padding: 8px 16px;
  transition: border-color 0.2s, box-shadow 0.2s;

  &:focus-within {
    border-color: #4e6bff;
    box-shadow: 0 2px 12px rgba(78, 107, 255, 0.12);
  }

  .input-text {
    flex: 1;
    padding: 8px 0;
    font-size: 15px;
    border: none;
    outline: none;
    background: transparent;
  }

  .send-icon {
    font-size: 24px;
    color: #ccc;
    cursor: pointer;
    transition: color 0.2s;
    margin-left: 8px;
  }

  .send-icon.has-content {
    color: #4e6bff;
  }
}
</style>
