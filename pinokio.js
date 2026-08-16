module.exports = {
  version: "7.0",
  title: "Unlimited OCR – Searchable PDF",
  description: "Local Baidu Unlimited-OCR with live rendered Markdown and optional Searchable Scan or Reconstructed PDF output.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const installed = info.exists("env") && info.exists("models/Unlimited-OCR/config.json")
    const installing = info.running("install.js")
    const running = info.running("start.js")

    if (installing) {
      return [
        { icon: "fa-solid fa-circle-notch fa-spin", text: "Installing…", href: "install.js" }
      ]
    }

    if (running) {
      const local = info.local("start.js")
      if (local && local.url) {
        return [
          // No target:'_blank': Pinokio opens this URL in its own built-in web surface.
          { icon: "fa-solid fa-rocket", text: "Open Web UI", href: local.url, default: true },
          { icon: "fa-solid fa-terminal", text: "Server", href: "start.js" }
        ]
      }
      return [
        { icon: "fa-solid fa-circle-notch fa-spin", text: "Loading model…", href: "start.js", default: true }
      ]
    }

    if (installed) {
      return [
        { icon: "fa-solid fa-play", text: "Start", href: "start.js", default: true },
        { icon: "fa-solid fa-terminal", text: "Terminal", href: "?selected=terminal" },
        { icon: "fa-solid fa-rotate", text: "Reinstall / Repair", href: "install.js" }
      ]
    }

    return [
      { icon: "fa-solid fa-download", text: "Install", href: "install.js", default: true }
    ]
  }
}
