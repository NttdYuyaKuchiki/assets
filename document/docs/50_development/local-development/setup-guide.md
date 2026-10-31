# 環境構築手順

## ツールインストール（Mac）

### Homebrew

1. 下記コマンドでHomebrewをインストールする

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. パスを通す

```bash
vi ~/.zshrc

export PATH="$PATH:/opt/homebrew/bin/"

source ~/.zshrc
```

3. Homebrewがインストールされたことを確認する

```bash
brew --version
```

### Python

1. 下記コマンドでuvをインストールする

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Zscaler環境では証明書検証に失敗するため、uvにOSの証明書ストア（キーチェーン）を使わせる

```bash
vi ~/.zshrc

export UV_SYSTEM_CERTS=true

source ~/.zshrc
```

3. パスを通す

```bash
vi ~/.zshrc

autoload -Uz compinit
compinit

eval "$(uv generate-shell-completion zsh)"
eval "$(uvx --generate-shell-completion zsh)"

source ~/.zshrc
```

1. 下記コマンドでPython14をインストールする

```bash
uv python install 3.14
```

5. Pythonがインストールされたことを確認する

```bash
uv python list
```

### Docker

1. 下記コマンドでDockerをインストールする

```bash
brew install colima docker
```

2. インストールされたことを確認する

```bash
colima --version
docker -v
```

3. docker compose をインストール

```bash
mkdir -p ~/.docker/cli-plugins

curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-darwin-aarch64 \
  -o ~/.docker/cli-plugins/docker-compose

chmod +x ~/.docker/cli-plugins/docker-compose
```

4. インストールされたことを確認する

```bash
docker compose version
```

5. 起動することを確認する

```bash
colima start
docker ps
```

6. Zscalerの設定

```bash
colima ssh
sudo mkdir -p /usr/local/share/ca-certificates
exit

scp ZscalerRootCertificate-2048-SHA256.crt colima:/tmp/

colima ssh
sudo cp /tmp/ZscalerRootCertificate-2048-SHA256.crt /usr/local/share/ca-certificates/zscaler.crt
sudo update-ca-certificates
exit

colima restart
```

7. 停止する

```bash
colima stop
```

## その他ツール

下記URLよりダウンロードする

- Visual Studio Code<br />
  https://code.visualstudio.com/

- Sourcetree<br />
  https://www.sourcetreeapp.com/

- MySQL Workbench<br />
  https://dev.mysql.com/downloads/workbench/

- NoSQL Workbench<br />
  https://docs.aws.amazon.com/ja_jp/amazondynamodb/latest/developerguide/workbench.settingup.html
