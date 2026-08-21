module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        venv: "env",
        message: [
          "python app/download_katex.py --if-missing --optional",
          "python app/app.py"
        ],
        on: [
          {
            event: "/PINOKIO_URL=(http:\\/\\/\\S+)/",
            done: true
          }
        ]
      }
    },
    {
      method: "local.set",
      params: {
        url: "{{input.event[1]}}"
      }
    }
  ]
}
