const stageMeta = {
  safe: {
    label: "当前可正常赶上",
    title: "建议 17:05 前出发",
    pill: "安全余量 18 分钟",
    eta: "43 分钟",
    buffer: "15 分钟",
    theme: "safe",
  },
  first: {
    label: "第一次提醒",
    title: "现在出发还来得及",
    pill: "请立即决策",
    eta: "43 分钟",
    buffer: "15 分钟",
    theme: "warn",
  },
  failed: {
    label: "原站方案已失效",
    title: "启动出行救急",
    pill: "赶上概率 < 20%",
    eta: "78 分钟",
    buffer: "15 分钟",
    theme: "danger",
  },
  action: {
    label: "已找到最小损失方案",
    title: "去石家庄接回原车",
    pill: "成功概率 78%",
    eta: "54 分钟",
    buffer: "12 分钟",
    theme: "action",
  },
};

const panels = {
  safe: document.querySelector("#safePanel"),
  first: document.querySelector("#firstPanel"),
  failed: document.querySelector("#failedPanel"),
  action: document.querySelector("#actionPanel"),
};

const statusCard = document.querySelector("#statusCard");
const statusLabel = document.querySelector("#statusLabel");
const statusTitle = document.querySelector("#statusTitle");
const statusPill = document.querySelector("#statusPill");
const etaText = document.querySelector("#etaText");
const bufferText = document.querySelector("#bufferText");
const tabs = [...document.querySelectorAll("[data-stage]")];
const labDrawer = document.querySelector("#labDrawer");
const consoleResult = document.querySelector("#consoleResult");

function setStage(stage) {
  const meta = stageMeta[stage];
  if (!meta) return;

  Object.entries(panels).forEach(([key, panel]) => {
    panel.classList.toggle("active", key === stage);
  });

  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.stage === stage);
  });

  statusLabel.textContent = meta.label;
  statusTitle.textContent = meta.title;
  statusPill.textContent = meta.pill;
  etaText.textContent = meta.eta;
  bufferText.textContent = meta.buffer;
  statusCard.dataset.theme = meta.theme;
  document.body.dataset.stage = meta.theme;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => setStage(tab.dataset.stage));
});

document.querySelectorAll("[data-stage-button]").forEach((button) => {
  button.addEventListener("click", () => setStage(button.dataset.stageButton));
});

document.querySelector("[data-open-lab]").addEventListener("click", () => {
  labDrawer.classList.add("open");
  labDrawer.setAttribute("aria-hidden", "false");
});

document.querySelector("[data-close-lab]").addEventListener("click", () => {
  labDrawer.classList.remove("open");
  labDrawer.setAttribute("aria-hidden", "true");
});

labDrawer.addEventListener("click", (event) => {
  if (event.target === labDrawer) {
    labDrawer.classList.remove("open");
    labDrawer.setAttribute("aria-hidden", "true");
  }
});

const consoleMessages = {
  eta: "打车 ETA 增加 8 分钟，原推荐保留，但失败阈值提前到 17:55。",
  queue: "进站排队增加 10 分钟，系统降低成功概率并提示优先叫车。",
  ticket: "补救车余票消失，Run! 切换到 Plan B：改签下一班高铁。",
  delay: "原车晚点 12 分钟，下游拦截成功概率升至 86%。",
};

document.querySelectorAll("[data-console]").forEach((button) => {
  button.addEventListener("click", () => {
    setStage("action");
    consoleResult.textContent = consoleMessages[button.dataset.console];
  });
});

setStage("safe");
