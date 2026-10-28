// Raydium LaunchLab transaction builder for devbot (LetsBonk is a LaunchLab platform).
//
// Pure builder: one JSON request on stdin, one JSON reply on stdout, no private keys. Python supplies
// public keys, gets back UNSIGNED transactions (base64 v0), signs them locally and sends.
//
//   launch           {rpc, payer, mint, name, symbol, uri, platformId?, buyLamports, microLamports}   -> {txs, platformId}
//   platform-create  {rpc, payer, name, web, img, feeRate, creatorFeeRate, lockScales, microLamports}  -> {txs, platformId}
//   unclaimed        {rpc, creator, platformId?}                              -> {lamports, platformLamports, own}
//   claim-creator    {rpc, payer, microLamports}                                                      -> {txs}
//   claim-platform   {rpc, payer, platformId, microLamports}                                          -> {txs}
//
// Units: LaunchLab rates are parts per 1,000,000 (feeRate 5000 = 0.5%).
import {
  LAUNCHPAD_PROGRAM, PlatformConfig, Raydium, TxVersion, getPdaCreatorVault, getPdaLaunchpadConfigId,
  getPdaPlatformId, getPdaPlatformVault, LAUNCHPAD_PLATFORM,
} from "@raydium-io/raydium-sdk-v2";
import { Connection, PublicKey, SystemProgram } from "@solana/web3.js";
import BN from "bn.js";

const NATIVE_MINT = new PublicKey("So11111111111111111111111111111111111111112");
const UNITS = 400_000;

async function load(req, owner) {
  const connection = new Connection(req.rpc, "confirmed");
  const raydium = await Raydium.load({
    connection, owner: new PublicKey(owner), disableLoadToken: true, disableFeatureCheck: true,
  });
  return { connection, raydium };
}

function budget(req) {
  return req.microLamports ? { units: UNITS, microLamports: req.microLamports } : undefined;
}

const b64 = (tx) => Buffer.from(tx.serialize()).toString("base64");
const txsOf = (r) => (r.transactions ?? [r.transaction]).map(b64);

const handlers = {
  async launch(req) {
    const { raydium } = await load(req, req.payer);
    const configId = getPdaLaunchpadConfigId(LAUNCHPAD_PROGRAM, NATIVE_MINT, 0, 0).publicKey;
    const platformId = req.platformId ? new PublicKey(req.platformId) : LAUNCHPAD_PLATFORM;
    const buy = new BN(String(req.buyLamports || 0));
    const r = await raydium.launchpad.createLaunchpad({
      programId: LAUNCHPAD_PROGRAM, mintA: new PublicKey(req.mint), name: req.name, symbol: req.symbol,
      uri: req.uri, migrateType: "cpmm", configId, platformId, decimals: 6,
      buyAmount: buy, createOnly: buy.isZero(), slippage: new BN(100), // 1%; the buy is atomic with the create
      txVersion: TxVersion.V0, computeBudgetConfig: budget(req),
    });
    return { txs: txsOf(r), platformId: platformId.toBase58() };
  },

  async "platform-create"(req) {
    const { connection, raydium } = await load(req, req.payer);
    const admin = new PublicKey(req.payer);
    // Same CPMM fee tier Raydium's own platform migrates into.
    const base = PlatformConfig.decode((await connection.getAccountInfo(LAUNCHPAD_PLATFORM)).data);
    const [platformScale, creatorScale, burnScale] = req.lockScales;
    const r = await raydium.launchpad.createPlatformConfig({
      programId: LAUNCHPAD_PROGRAM, platformAdmin: admin, platformClaimFeeWallet: admin,
      platformLockNftWallet: admin, cpConfigId: base.cpConfigId,
      platformVestingWallet: req.vestingWallet ? new PublicKey(req.vestingWallet) : admin,
      migrateCpLockNftScale: {
        platformScale: new BN(platformScale), creatorScale: new BN(creatorScale), burnScale: new BN(burnScale),
      },
      transferFeeExtensionAuth: SystemProgram.programId,
      creatorFeeRate: new BN(req.creatorFeeRate), feeRate: new BN(req.feeRate),
      name: req.name, web: req.web, img: req.img, txVersion: TxVersion.V0, computeBudgetConfig: budget(req),
    });
    return { txs: txsOf(r), platformId: getPdaPlatformId(LAUNCHPAD_PROGRAM, admin).publicKey.toBase58() };
  },

  async unclaimed(req) {
    const connection = new Connection(req.rpc, "confirmed");
    const creator = new PublicKey(req.creator);
    const balance = async (vault) => {
      try {
        return (await connection.getTokenAccountBalance(vault)).value.amount;
      } catch {
        return "0"; // vault not created yet: nothing of ours has traded
      }
    };
    const ownId = getPdaPlatformId(LAUNCHPAD_PROGRAM, creator).publicKey;
    const own = !!req.platformId && ownId.equals(new PublicKey(req.platformId));
    return {
      lamports: await balance(getPdaCreatorVault(LAUNCHPAD_PROGRAM, creator, NATIVE_MINT).publicKey),
      platformLamports: own ? await balance(getPdaPlatformVault(LAUNCHPAD_PROGRAM, ownId, NATIVE_MINT).publicKey) : "0",
      own,
    };
  },

  async "claim-creator"(req) {
    const { raydium } = await load(req, req.payer);
    const r = await raydium.launchpad.claimCreatorFee({
      programId: LAUNCHPAD_PROGRAM, mintB: NATIVE_MINT, txVersion: TxVersion.V0, computeBudgetConfig: budget(req),
    });
    return { txs: txsOf(r) };
  },

  async "claim-platform"(req) {
    const { raydium } = await load(req, req.payer);
    const r = await raydium.launchpad.claimAllPlatformFee({
      programId: LAUNCHPAD_PROGRAM, platformId: new PublicKey(req.platformId),
      platformClaimFeeWallet: new PublicKey(req.payer), txVersion: TxVersion.V0, computeBudgetConfig: budget(req),
    });
    return { txs: txsOf(r) };
  },
};

const cmd = process.argv[2];
if (!handlers[cmd]) {
  console.error(`unknown command ${cmd}; expected one of ${Object.keys(handlers).join(", ")}`);
  process.exit(2);
}
let raw = "";
for await (const chunk of process.stdin) raw += chunk;
try {
  console.log(JSON.stringify(await handlers[cmd](JSON.parse(raw))));
} catch (e) {
  console.error(String(e.message || e).slice(0, 300));
  process.exit(1);
}
