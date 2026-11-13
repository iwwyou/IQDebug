# W2 준비 스크립트 (WALKTHROUGH.md §3). anvil 이 다른 창에서 실행 중이어야 한다.
# 사용: playground\forge 에서   . .\w2_setup.ps1     (앞의 점 + 공백 = 변수를 현재 창에 남긴다)
# 하는 일: Vault 배포 → mapping 슬롯 계산 → 주입 문자열 $OV 생성. 이후 명령은 WALKTHROUGH.md §3.

$F   = (Resolve-Path "$PSScriptRoot\..\..\forge\bin").Path
$RPC = "http://127.0.0.1:8545"
$ME  = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"   # anvil 0번 테스트 계정 (공개 키)
$KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

# ① 배포: 생성자 실행 → feeBps = 30
$out = & "$F\forge.exe" create src/Vault.sol:Vault --rpc-url $RPC --private-key $KEY --broadcast
$out | Select-String "Deployed to"
$V = (($out | Select-String "Deployed to").ToString() -split "\s+")[2]

# ② 슬롯 계산: shares 는 slot 0 의 mapping → keccak256(abi.encode(ME, 0))
$SLOT = & "$F\cast.exe" index address $ME 0

# ③ 주입 문자열: shares[ME] = 1000, totalShares(slot 1) = 1000
$OV = "${V}:${SLOT}:1000,${V}:0x1:1000"

"V    = $V"
"SLOT = $SLOT"
"OV   = $OV"
