// Public examples use only the fixtures below. Each step names a module in the diagram.
(() => {
  const home = document.getElementById("home-panel");
  const panel = document.getElementById("home-walkthrough");
  const diagram = document.getElementById("agent-diagram");
  const steps = document.getElementById("walk-steps");
  const play = document.getElementById("walk-play");
  const previous = document.getElementById("walk-prev");
  const next = document.getElementById("walk-next");
  const skill = {
    module: "skill", label: "Skill", title: "加载售后答复规范",
    detail: "运行时加载当前发布的 Skill。它要求每条政策说明都标注证据编号。",
    artifact: "support.answer",
  };
  const scenes = {
    returns: {
      question: "未拆封商品在签收后几天内可以申请退货？",
      steps: [
        skill,
        {module: "tool", label: "Tool", title: "检索退货政策",
          detail: "knowledge.search 在可读取的资料中找到一条退货条款。",
          artifact: "「演示商城未拆封商品自签收之日起七天内可申请退货。」"},
        {module: "context", label: "上下文", title: "将条款编入上下文",
          detail: "条款标为 E1，与问题和答复规范一同进入请求。快照保存这条引用对应的来源、版本和位置。",
          artifact: "E1 → return-policy · v1 · section-1"},
        {module: "model", label: "模型", title: "依据条款生成回答",
          detail: "通过准入检查后，模型读取上下文，回答退货期限并给出引用。",
          artifact: "未拆封商品可在签收之日起七天内申请退货。[E1]"},
        {module: "trace", label: "轨迹", title: "核对回答与调用记录",
          detail: "答复为“未拆封商品自签收之日起七天内可申请退货 [E1]”。快照中的 E1 指向本次使用的退货条款。",
          artifact: "Skill 加载 1 次 · Tool 执行 1 次 · 模型准入 1 次"},
      ],
    },
    invoice: {
      question: "电子发票补开需要哪些材料？",
      steps: [
        skill,
        {module: "tool", label: "Tool", title: "查找发票补开条款",
          detail: "样例资料库只有退货政策。knowledge.search 检索可读取的资料后，返回空结果。",
          artifact: "检索命中 0 条"},
        {module: "context", label: "上下文", title: "在证据检查处停止",
          detail: "上下文服务找不到可引用的来源，结束本次准备。问题和条款都没有发送给模型。",
          artifact: "MISSING_EVIDENCE"},
        {module: "trace", label: "轨迹", title: "定位停止的环节",
          detail: "本次尝试以 MISSING_EVIDENCE 结束。要回答发票问题，需要先补充有权读取的开票条款。",
          artifact: "Skill 加载 1 次 · Tool 执行 1 次 · 模型准入 0 次"},
      ],
    },
  };

  let scene = scenes.returns;
  let position = 0;
  let timer = null;

  function render() {
    const step = scene.steps[position];
    const last = position === scene.steps.length - 1;
    const playing = timer !== null;
    diagram.dataset.active = step.module;
    for (const [index, button] of steps.querySelectorAll("button").entries()) {
      if (index === position) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.classList.toggle("is-complete", index < position);
    }
    document.getElementById("walk-step-title").textContent = step.title;
    document.getElementById("walk-description").textContent = step.detail;
    document.getElementById("walk-artifact").textContent = step.artifact;
    document.getElementById("walk-progress").textContent = (position + 1) + " / " + scene.steps.length;
    previous.disabled = position === 0;
    next.disabled = last;
    play.dataset.playing = String(playing);
    play.querySelector(".walk-play-text").textContent = playing ? "暂停" : last ? "重播" : "播放";
    play.querySelector(".walk-play-icon").textContent = playing ? "Ⅱ" : last ? "↺" : "▶";
  }

  function stopPlayback() {
    window.clearInterval(timer);
    timer = null;
  }

  function showStep(index) {
    stopPlayback();
    position = index;
    render();
  }

  function selectScene(name) {
    scene = scenes[name];
    for (const button of panel.querySelectorAll("[data-scene]")) {
      button.setAttribute("aria-pressed", String(button.dataset.scene === name));
    }
    document.getElementById("walk-question").textContent = scene.question;
    const items = scene.steps.map((step, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "walk-step";
      button.dataset.module = step.module;
      button.setAttribute("aria-label", "第 " + (index + 1) + " 步，" + step.label + "，" + step.title);
      const marker = document.createElement("span");
      marker.className = "walk-marker";
      marker.setAttribute("aria-hidden", "true");
      const nameLabel = document.createElement("span");
      nameLabel.className = "walk-step-name";
      nameLabel.textContent = step.label;
      button.append(marker, nameLabel);
      button.addEventListener("click", () => showStep(index));
      item.append(button);
      return item;
    });
    steps.replaceChildren(...items);
    showStep(0);
  }

  // Playback starts only on request, including when reduced motion is preferred.
  play.addEventListener("click", () => {
    if (timer !== null) {
      stopPlayback();
    } else {
      if (position === scene.steps.length - 1) position = 0;
      timer = window.setInterval(() => {
        if (home.hidden || document.hidden) {
          stopPlayback();
        } else {
          position++;
          if (position === scene.steps.length - 1) stopPlayback();
        }
        render();
      }, 3500);
    }
    render();
  });
  previous.addEventListener("click", () => showStep(position - 1));
  next.addEventListener("click", () => showStep(position + 1));
  for (const button of panel.querySelectorAll("[data-scene]")) {
    button.addEventListener("click", () => selectScene(button.dataset.scene));
  }
  function pauseIfPlaying() {
    if (timer !== null) showStep(position);
  }
  new MutationObserver(() => {
    if (home.hidden) pauseIfPlaying();
  }).observe(home, {attributes: true, attributeFilter: ["hidden"]});
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pauseIfPlaying();
  });
  window.addEventListener("pagehide", pauseIfPlaying);
  selectScene("returns");
})();
