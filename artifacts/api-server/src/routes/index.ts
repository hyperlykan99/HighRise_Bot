import { Router, type IRouter } from "express";
import healthRouter from "./health";
import djRouter from "./dj";

const router: IRouter = Router();

router.use(healthRouter);
router.use(djRouter);

export default router;
