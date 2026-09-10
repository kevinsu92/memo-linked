# 설계와 운영

## 권한 모델

계정 없이도 남의 메모가 지워지지 않도록 소유 토큰을 쓴다.

1. 메모를 저장하면 서버가 임의 토큰을 만들어 SHA-256 해시만 저장하고, 평문은 응답에
   한 번만 담아 보낸다.
2. 브라우저가 그 토큰을 `localStorage` 에 보관한다.
3. 수정·삭제·고정 요청은 토큰 해시가 맞아야 통과한다. 좋아요는 파괴적이지 않아
   토큰을 요구하지 않는다. 다만 취소 기능이 없어 되돌릴 수는 없다.
4. 소유자 정보가 없는 예전 메모는 `ADMIN_TOKEN` 이 있어야 고칠 수 있다.

브라우저 저장소를 지우면 그 메모를 더는 고칠 수 없다. 계정이 없는 구조의 한계이며,
실제 서비스라면 세션 로그인이 먼저다.

## 이 메모장만의 기능

### 메모끼리 잇기

내용에 `[[제목]]` 을 적으면 그 제목을 가진 메모로 이어진다. 제목으로 걸리므로 아직
쓰지 않은 메모도 미리 가리킬 수 있고, 나중에 그 제목으로 메모를 만들면 링크가
저절로 연결된다. 대상 메모에서는 자기를 가리키는 메모 목록(백링크)을 볼 수 있다.

연결 그래프는 물리 시뮬레이션을 애니메이션 없이 정해진 횟수만 돌려 좌표를 정한다.
결과가 매번 같고 저사양 기기에서도 부담이 없다. 원의 크기는 연결 수를 나타낸다.

`Ctrl+K` 명령 팔레트로 제목만 쳐서 이동할 수 있다.

### 기록 남기기

메모를 고치면 직전 내용이 이력으로 남는다. 메모당 최근 20개를 유지하며, 현재
내용과 무엇이 다른지 줄 단위로 보여 준다. 되돌리기를 해도 그 직전 상태가 다시
이력에 쌓이므로 되돌리기를 되돌릴 수 있다.

활동 히트맵은 날짜별 작성 수를 잔디밭처럼 보여 주고 연속 작성 일수를 센다.

### 비밀 지키기

잠금 메모는 브라우저에서 PBKDF2(SHA-256, 20만 회)로 키를 만들고 AES-GCM 256 으로
감싼 뒤 그 결과만 서버로 보낸다. 서버는 `memo1.<salt>.<iv>.<암호문>` 형식인지만
확인하고 그대로 보관한다. 비밀번호는 어디에도 저장되지 않는다.

타임캡슐은 지정한 때가 되기 전까지 서버가 어떤 경로로도 내용을 내보내지 않는다.
목록, 이력 조회, 공유 링크를 모두 막고 검색 대상에서도 뺀다. 검색이 되면 "이 낱말이
들어 있는가" 를 맞혀 보는 통로가 되기 때문이다.

다만 서버는 평문을 갖고 있으므로 남에게 감춰야 하는 내용은 잠금 메모를 써야 한다.

공유 링크는 토큰의 해시만 저장한다. "한 번 읽으면 사라지게" 를 켜면 주소를 열었을
때 바로 보여 주지 않고 확인 화면을 먼저 띄운다. 메신저의 링크 미리보기 봇이나 메일
보안 검사기가 주소를 긁는 것만으로 사라지는 것을 막기 위해서다. 사람이 버튼을
눌러야 내용이 보이고 그 즉시 소각된다.

소각된 메모는 데이터베이스에 30일 남지만 화면에서 되살릴 방법은 없다. 삭제 직후
뜨는 실행 취소 토스트를 놓치면 id 를 알 수 없기 때문이다. 휴지통 화면은 아직 없다.

## 설계 판단과 그 이유

**검색에 정규식을 쓴 이유.** MongoDB 기본 텍스트 인덱스에는 한국어 형태소 분석기가
없어 `파이썬공부` 안의 `파이썬` 을 찾지 못한다. 부분 문자열 검색이 필수라 `$regex`
를 쓰되, `re.escape` 로 메타문자를 막고 검색어 길이를 50자로 자르며 `maxTimeMS` 로
실행 시간을 2초로 제한했다. 데이터가 수만 건을 넘으면 n-gram 인덱스나 외부 검색
엔진으로 옮겨야 한다.

**목록 조회에서 개수를 분리한 이유.** 예전에는 `count_documents` 와 `find` 가 같은
조건으로 컬렉션을 두 번 훑었다. 지금은 `size + 1` 건을 읽어 다음 페이지 유무만
판단하고, 전체 개수는 `/memo/count` 로 따로 받으며 상한을 둔다.

**정렬마다 인덱스를 둔 이유.** 정렬 정의와 인덱스 생성을 `memo/db.py` 의 같은
자료구조에서 관리한다. 한쪽만 추가해 인메모리 정렬로 떨어지는 사고를 막고, 테스트가
`explain` 으로 정렬과 태그 필터의 모든 조합이 인덱스를 타는지 확인한다.

**삭제를 소프트 삭제로 한 이유.** 백업 주기가 하루라 그 사이에 지운 메모는 되돌릴
방법이 없었다. 지금은 삭제 표시만 하고 30일 뒤 MongoDB 의 TTL 인덱스가 실제로
지운다. 그동안은 실행 취소로 좋아요 수와 작성 시각까지 그대로 복구된다.

**레이트 리밋 카운터를 MongoDB 에 둔 이유.** 워커가 여럿이라 메모리 저장소를 쓰면
카운터가 프로세스마다 따로 세어 실효 한도가 배로 늘어난다. 이미 쓰고 있는 MongoDB
를 공유 저장소로 재사용했다. 운영 환경에서 메모리 저장소가 설정되면 기동을 거부한다.

**워커를 2개로 둔 이유.** 인스턴스 메모리가 1GB 미만이고 같은 기기에서 MongoDB 가
돌기 때문이다. gthread 워커에 스레드 4개를 두어 DB 대기 중에도 다른 요청을 처리하고,
systemd 의 `MemoryMax` 로 메모리가 부족할 때 앱이 먼저 정리되게 했다.

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| MONGO_URI | mongodb://localhost:27017 | MongoDB 접속 주소 |
| DB_NAME | memo | 데이터베이스 이름 |
| HOST / PORT | 127.0.0.1 / 5000 | 개발 서버 바인드 |
| FLASK_DEBUG | 0 | 1 은 로컬 전용. 서버에서 켜면 원격 코드 실행 위험 |
| LOG_LEVEL | INFO | 잘못된 값이면 기동 시점에 바로 실패 |
| APP_ENV | development | production 이면 보안 설정이 비었을 때 기동 거부 |
| APP_REVISION | unknown | 배포된 커밋. `/healthz` 가 알린다 |
| SITE_ORIGIN | (없음) | 교차 사이트 쓰기 차단 기준 |
| ADMIN_TOKEN | (없음) | 소유자 없는 예전 메모를 고칠 때 쓰는 토큰 |
| RATELIMIT_* | 아래 참고 | 기본 120/분, 쓰기 20/분 |

`MAX_TITLE`, `MAX_CONTENT`, `MAX_MEMOS`, `COUNT_LIMIT`, `QUERY_TIMEOUT_MS` 등 동작
한도도 모두 환경 변수로 바꿀 수 있다. 기본값은 `memo/config.py` 참고.

## 전체 API

| 메서드 | 경로 | 파라미터 | 설명 |
|---|---|---|---|
| GET | /memo | q, tag, sort, page, size, shift | 목록 |
| GET | /memo/count | q, tag | 개수 (상한 있음) |
| GET | /tags | - | 태그와 사용 횟수 (60초 캐시) |
| GET | /healthz | - | 헬스 체크. 외부에서는 차단 |
| POST | /memo | title_give, content_give, tags_give | 저장 (201) |
| POST | /memo/update | id_give, token_give, title_give, content_give, tags_give | 수정 |
| POST | /memo/delete | id_give, token_give | 삭제 |
| POST | /memo/restore | id_give, token_give | 삭제 취소 |
| POST | /memo/like | id_give | 좋아요 (토큰 불필요) |
| POST | /memo/pin | id_give, token_give | 고정 토글 |
| GET | /memo/&lt;id&gt;/links | - | 링크와 백링크 |
| GET | /memo/&lt;id&gt;/revisions | - | 수정 이력과 차이 |
| POST | /memo/revision/restore | id_give, token_give, revision_give | 되돌리기 |
| GET | /graph | - | 연결 그래프 |
| GET | /stats/activity | days | 활동 히트맵 자료 |
| GET | /stats/summary | - | 요약 통계 |
| GET | /search/quick | q | 제목 빠른 검색 |
| POST | /memo/share | id_give, token_give, burn_give, revoke | 공유 링크 |
| GET | /s/&lt;token&gt; | - | 공유 읽기 전용 화면 |
| POST | /memo/capsule | id_give, token_give, open_at_give | 타임캡슐 |

성공은 HTTP 2xx 와 `{"result": "success", ...}`, 실패는 상태 코드
(400 / 403 / 404 / 413 / 429 / 503 / 507) 와 `{"result": "fail", "msg": "..."}` 를
돌려준다. 모든 응답에 요청 추적용 `X-Request-ID` 헤더가 붙는다.

## 배포

```
인터넷 → nginx (80/443) → gunicorn (127.0.0.1:5000) → MongoDB (127.0.0.1:27017)
```

앱 포트는 외부에 열지 않는다. gunicorn 이 루프백에만 바인딩하므로 nginx 를 거치지
않고는 접근할 수 없다. systemd 유닛의 경로는 `/home/ubuntu/memo` 를 전제로 한다.
다른 경로에 두려면 `deploy/memo.service` 와 `deploy/nginx-memo.conf` 를 함께 고친다.

### 최초 배포

```bash
git clone <저장소 주소> ~/memo && cd ~/memo
DOMAIN=<도메인> bash deploy.sh
DOMAIN=<도메인> ALERT_WEBHOOK=<웹훅 주소> bash scripts/install-watch.sh
```

`deploy.sh` 가 하는 일: 필수 파일 확인 → 디스크 여유 확인 → 코드 갱신 → 패키지
설치 → 가상환경 → `.env` 정비 → 인증서 발급 → nginx 설정 → 인덱스/마이그레이션
1회 실행 → systemd 기동 → 헬스 체크.

### 코드 갱신

```bash
cd ~/memo && DOMAIN=<도메인> SKIP_APT=1 bash deploy.sh
```

`git pull` 을 스크립트 밖에서 하지 않는다. 배포 스크립트가 pull 하기 **전에** 현재
리비전을 기록해야 롤백이 의미를 갖기 때문이다. 헬스 체크가 실패하면 코드와 nginx
설정, systemd 유닛을 함께 되돌리고 다시 확인한다. 배포가 끝나면 `/healthz` 가 알리는
리비전이 배포 대상과 같은지, 외부에서 HTTPS 접속이 되는지까지 확인한다.

### MongoDB 인증

인증을 켜는 경우 앱 전용 사용자를 만들고 `.env` 의 `MONGO_URI` 에 반영한다.

```javascript
use memo
db.createUser({
  user: 'memo_app',
  pwd: '<강한 임의 문자열>',
  roles: [{ role: 'readWrite', db: 'memo' }],
})
```

```
MONGO_URI=mongodb://memo_app:<비밀번호>@127.0.0.1:27017/memo?authSource=memo
```

레이트 리밋 저장소는 이 주소에서 파생되므로 따로 적지 않는다.

### 서버 접근

관리 접속은 SSH 키로만 받는다. 비밀번호 로그인과 root 직접 로그인을 끄고,
fail2ban 으로 반복 시도를 차단한다.

```bash
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin|pubkeyauthentication)'
sudo fail2ban-client status sshd
```

22 번을 특정 주소에서만 열도록 좁히고 싶은 유혹이 있는데, 접속 회선이 무엇인지
먼저 봐야 한다. 카페나 공유 사무실처럼 여럿이 나눠 쓰는 망은 공인 주소 하나를
같이 쓰므로, 그 주소로 좁혀 봐야 같은 망에 있는 모두를 들여보내는 셈이 된다.
보호는 거의 없으면서 자리를 옮기면 자기만 잠긴다. 가정용 회선도 주소가 바뀐다.

주소로 좁히는 것이 뜻대로 되려면 고정 주소가 있어야 한다. 그렇지 않다면 22 번을
인터넷 쪽으로 아예 닫고 클라우드 제공자의 접속 경로(EC2 Instance Connect 등)를
쓰는 편이 낫다. 어느 쪽도 아니라면 키 인증과 fail2ban 으로 두는 것이 무난하다.

개인 키는 클라우드에 동기화되는 폴더에 두지 않는다. 그 파일 하나로 서버에
들어올 수 있으므로 서버 쪽 설정보다 이쪽이 먼저 새기 쉽다.

## 운영

```bash
sudo systemctl status memo          # 앱 상태
sudo journalctl -u memo -n 50       # 앱 로그 (요청 id 포함)
journalctl -t memo-watch -p err     # 감시 경고
bash scripts/backup.sh              # 수동 백업
bash scripts/restore.sh             # 최신 백업으로 복구
```

`memo-backup.timer` 가 매일 백업하고 최근 14개를 남긴다. 백업 직후 복원 가능한
아카이브인지 검증한다. `memo-watch.timer` 는 5분마다 앱·DB·서비스·디스크·메모리·
외부 접속·인증서 만료·백업 신선도를 확인하고, 문제가 있으면 journald 에 error 로
남기고 `ALERT_WEBHOOK` 이 있으면 웹훅으로도 보낸다.

백업이 같은 디스크에만 있으면 볼륨 손상 시 함께 사라진다. 주기적으로 다른 곳으로
내려받아 둘 것.

## 장애 대응

| 경고 | 확인 | 조치 |
|---|---|---|
| 헬스 체크 실패 | `journalctl -u memo -n 50` | 설정 오류면 `.env` 확인 후 재시작 |
| mongod 정지 | `journalctl -u mongod -n 50` | 대개 설정 문법 오류. `mongod --config ... --outputConfig` 로 검증 |
| 디스크 85% 초과 | `du -sh /var/log /var/lib/mongodb ~/backups` | `apt-get clean`, journald 정리, 오래된 백업 삭제 |
| 가용 메모리 부족 | `systemctl status memo` | `MemoryMax` 에 걸렸는지 확인. 워커 수 조정 |
| 외부 접속 실패 | `nginx -t`, 방화벽 규칙 | 정적 파일 404 면 홈 디렉터리 탐색 권한 확인 |
| 인증서 만료 임박 | `certbot renew --dry-run` | 실패 원인 확인 후 `systemctl reload nginx` |
| 백업 없음 | `bash scripts/backup.sh` | 인증 정보가 바뀌면 `.env` 의 MONGO_URI 확인 |

RPO 는 24시간이다. 백업 주기가 하루이므로 그 사이 작성분은 복구되지 않는다.

## 재해 복구

1. 새 인스턴스를 만들고 22/80/443 을 연다.
2. MongoDB 를 설치한다 (`mongodb-org` 저장소 등록 후 설치).
3. `git clone` 후 `DOMAIN=<도메인> bash deploy.sh`.
4. 백업을 올리고 `bash scripts/restore.sh <백업파일>`.
5. DNS 를 새 주소로 돌린다.

## 남은 과제

- 계정과 세션 기반 로그인
- 소유 토큰 내보내기·가져오기 (지금은 브라우저를 바꾸면 권한을 잃는다)
- 메모 내보내기 (마크다운·JSON 백업)
- 휴지통 화면 (소프트 삭제한 메모를 화면에서 되살리기)
- 제목 중복 방지 또는 링크 모호성 해소
- 커서 기반 페이지네이션 (지금은 `skip`, 뒤 페이지일수록 느려짐)
- 히트맵의 현지 시각 처리 (지금은 UTC 기준)
- 백업을 서버 밖으로 자동 회수
