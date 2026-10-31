# A0101 ユーザー情報取得(自分)

## 概要

ログイン中のユーザー自身のプロフィールをユーザー情報から取得する。ユーザーは ID トークンの `sub` で特定する。

## シーケンス図

```mermaid
sequenceDiagram
    participant App as 画面
    participant UserServer as ユーザーECS
    participant Db as RDS（レプリカ）

    App->>UserServer: ユーザー情報取得(自分)APIリクエスト
    activate UserServer
    Note right of UserServer: [L1001] 処理開始

    UserServer->>UserServer: 1. JWT検証
    Note over UserServer,Db: Authorizationヘッダーの IDトークンを検証<br/>署名（CognitoのJWKS。キャッシュする）<br/>iss（User Pool） / aud（App Client ID）<br/>exp / token_use = id / sub取得
    alt 検証エラー
        Note right of UserServer: [L2004] 認証エラー
        UserServer->>App: 認証エラー
    end

    UserServer->>UserServer: 2. ユーザー情報取得(自分)
    Note over UserServer,Db: SELECT id, email, display_name<br/>FROM users<br/>WHERE cognito_sub = :sub
    Note right of UserServer: [L1201] SQLクエリ
    UserServer->>Db: ユーザー情報取得(自分)
    Db->>UserServer:
    alt SQL実行エラー
        Note right of UserServer: [L1212] SQL実行エラー
        UserServer->>App: システムエラー
    else 該当0件
        Note right of UserServer: [L1202] 取得件数0件（未登録）
        UserServer->>App: 未登録エラー
    end

    UserServer->>UserServer: 3. レスポンス編集
    Note right of UserServer: id = users.id<br/>email = users.email<br/>displayName = users.display_name
    Note right of UserServer: [L1002] 処理終了
    UserServer->>App: ユーザー情報取得(自分)APIレスポンス
    Note right of UserServer: [L2001] アクセスログ
    deactivate UserServer
```

- `sub`、`email`、トークンなどの個人情報・認証情報はログに出力しない。

## 参考資料

- [IF仕様書](pathname:///redoc/user.html#operation/getMe)
- [ログ一覧](../../../30_common-design/logging/log-catalog.md)
- [アーキテクチャ](../architecture.md)
